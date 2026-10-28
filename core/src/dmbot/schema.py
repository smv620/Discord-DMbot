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
    -- (a "batch") may change several rows. Not part of backups. The one exception: the quote
    -- kept with a name read from the DM's story (#2167) is blanked here too once the DM decides
    -- on the name, so none of the DM's text outlives the decision.
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

# The DM's recaps on the website (#1382): the signed-in person's own campaigns' recaps only, in
# their session's servers. The campaign's DM list is read under the same rule, so being a DM is
# what opens a row. Applied once the recap table exists.
WEB_ROLE_RECAP_POLICY = """
    DROP POLICY IF EXISTS web_dm_recaps ON recap_jobs;
    CREATE POLICY web_dm_recaps ON recap_jobs AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               AND EXISTS (SELECT 1 FROM campaign_dms d
                           WHERE d.guild_id = recap_jobs.guild_id
                             AND d.campaign_id = recap_jobs.campaign_id
                             AND d.user_id = dmbot_current_user()))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
                    AND EXISTS (SELECT 1 FROM campaign_dms d
                                WHERE d.guild_id = recap_jobs.guild_id
                                  AND d.campaign_id = recap_jobs.campaign_id
                                  AND d.user_id = dmbot_current_user()));
"""

# Applied only once the offers table has all four bot-only columns (an older schema, as in a test
# of a backfill, doesn't, and a rule can't name a column that isn't there).
WEB_ROLE_OFFER_POLICY = """
    -- Creating an offer (#728, #690 follow-up; #2532). The website's role may INSERT only the
    -- columns in OFFER_INSERT_COLUMNS, never the whole table, and this rule stays on top: an offer
    -- from the website is the signed-in owner's own, by email, still open, and not claimed or
    -- announced. Otherwise the site could write itself an offer on someone else's campaign.
    DROP POLICY IF EXISTS web_new_offer ON campaign_handover_offers;
    CREATE POLICY web_new_offer ON campaign_handover_offers AS RESTRICTIVE FOR INSERT TO dmbot_web
        WITH CHECK (via_email AND token_hash IS NOT NULL AND to_user_id = 0
                    -- an offer from the website is only ever by email (#2532): a Discord offer
                    -- would be sent to a person as buttons, which PLAN retires; it is born
                    -- delivered, because the bot has no Discord message to send for it
                    AND delivered_at = created_at
                    AND created_at BETWEEN dmbot_now() - 300 AND dmbot_now() + 300
                    AND claimed_at IS NULL AND message_id IS NULL
                    AND end_told_at IS NULL AND status = 'open' AND decided_at IS NULL
                    AND from_user_id = dmbot_current_user()
                    -- only the campaign's own owner can offer it: without this, anyone in the
                    -- server could write themselves an offer and then accept it
                    AND EXISTS (SELECT 1 FROM campaigns c
                                WHERE c.guild_id = campaign_handover_offers.guild_id
                                  AND c.id = campaign_handover_offers.campaign_id
                                  AND c.owner_user_id = campaign_handover_offers.from_user_id));
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

GAME_CLOCK_GROUPS = f"""
    -- Split-party clocks (#1607; docs/PLAN.md, "TimeBot"): while the party is split, each group
    -- has its own clock here, and `game_clocks` keeps the clock from before the split (it is
    -- the clock again when the party rejoins). Per campaign, never shared between campaigns or
    -- servers; deleted with the campaign; in its backups (position, name, time, last rest).
    -- At most three groups, changed only by a DM of the campaign (checked in the same
    -- transaction). A new table and nullable or defaulted columns: no row changes, so no
    -- backfill policy.
    CREATE TABLE game_clock_groups (
        {_memory_scope()}
        position       INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
        name           TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 20),
        minute         BIGINT NOT NULL CHECK (minute >= 0),
        last_long_rest BIGINT NOT NULL CHECK (last_long_rest >= 0),
        tired_told_for BIGINT CHECK (tired_told_for IS NULL OR tired_told_for >= 0),
        combat_round   INTEGER CHECK (combat_round IS NULL OR combat_round BETWEEN 1 AND 99),
        combat_secs    INTEGER NOT NULL DEFAULT 0 CHECK (combat_secs BETWEEN 0 AND 59),
        PRIMARY KEY (guild_id, campaign_id, position)
    );
    {_isolate("game_clock_groups")}
    -- picked_group: the group the buttons act on (remembered across restarts); NULL when the
    -- party is not split. The two told flags make the "split, so I didn't move the clock" line
    -- and the "9 hours ahead" line each be said once per split. split_undo is the snapshot
    -- the last Split or Rejoin's Undo needs (a token, and what to put back).
    ALTER TABLE game_clocks
        ADD COLUMN picked_group INTEGER
            CHECK (picked_group IS NULL OR picked_group BETWEEN 1 AND 3),
        ADD COLUMN split_phrase_told BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN split_drift_told BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN split_undo JSONB;
    -- A timer started while split counts on the group picked then (NULL: the first group, or
    -- the one clock when the party is not split).
    ALTER TABLE game_effects
        ADD COLUMN group_position INTEGER
            CHECK (group_position IS NULL OR group_position BETWEEN 1 AND 3);
"""

IMPROVEMENT_OPT_OUTS = """
    -- A person's choice not to have their words used to improve DMbot (#1609; docs/PLAN.md,
    -- "Data, ownership and deletion"). Per Discord account, across all servers: it is the
    -- person's choice, not a campaign's. A row means "opted out"; no row is the default (DMbot
    -- may use their words). Nothing is deleted by it, and running their table is unaffected.
    -- Kept when an account is deleted (no link to web_users): forgetting the choice would put
    -- their words back in. A person reads and writes only their own row (user_isolation); the
    -- exports read it one person at a time through Database.user(), so nothing here lets one
    -- person see another's choice. A new table: no rows change, so no backfill policy.
    CREATE TABLE improvement_opt_outs (
        user_id      BIGINT PRIMARY KEY CHECK (user_id > 0),
        opted_out_at BIGINT NOT NULL
    );
    """ + _isolate_user("improvement_opt_outs")

HOMEBREW_RULES_TO_HOUSE_RULES = """
    -- Rules are house rules, not homebrew (owner decision 2026-10-16, #1628; docs/PLAN.md,
    -- "Homebrew (part 1)"). Every homebrew entry of the kind 'rule' that is the DM's own
    -- and fits a house rule (one line of at most 500 characters, the name in front unless the
    -- text already starts with it; at most 200 house rules a campaign)
    -- becomes the campaign's next numbered house rule, and the entry goes. A rule from a shared
    -- book keeps its book confirmation, which a house rule cannot hold; one that does not fit
    -- and one past the 200 would lose their words: all stay as homebrew, retagged 'other'. A
    -- hidden rule moves too (owner, 2026-10-16, "Homebrew (part 2)": nothing is hidden from
    -- players any more). A linked house-rules file is not rewritten. Open suggestions
    -- of a 'rule' to a pack are dropped (a pack cannot hold one). The kind 'rule' leaves
    -- homebrew_entries' list.
    -- Row-level security (CLAUDE.md, migrations that change rows): migrations run with no server
    -- set, so each table read or written gets a transient policy that is dropped again below:
    -- homebrew_entries (read, delete, retag), house_rules (insert), campaigns (the counter) and
    -- pack_suggestions (delete).
    CREATE POLICY migrate_backfill ON homebrew_entries USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON house_rules USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON campaigns USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON pack_suggestions USING (true) WITH CHECK (true);

    CREATE TEMP TABLE moved_rules ON COMMIT DROP AS
        SELECT id, guild_id, campaign_id, created_by, created_at, updated_at, rule_text,
               row_number() OVER (PARTITION BY guild_id, campaign_id ORDER BY id) AS n
        FROM (
            SELECT id, guild_id, campaign_id, created_by, created_at, updated_at,
                   btrim(regexp_replace(
                       CASE WHEN lower(left(text, char_length(name))) = lower(name)
                            THEN text ELSE name || ': ' || text END,
                       '[[:space:]]+', ' ', 'g')) AS rule_text
            FROM homebrew_entries
            WHERE kind = 'rule' AND NOT from_book
        ) AS composed
        WHERE char_length(rule_text) <= 500;
    DELETE FROM moved_rules m
        WHERE m.n + (SELECT count(*) FROM house_rules h
                     WHERE h.guild_id = m.guild_id AND h.campaign_id = m.campaign_id) > 200;

    INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by, created_at,
                             updated_at)
        SELECT m.guild_id, m.campaign_id, c.house_rules_made + m.n, m.rule_text, m.created_by,
               m.created_at, m.updated_at
        FROM moved_rules m JOIN campaigns c ON c.guild_id = m.guild_id AND c.id = m.campaign_id;
    UPDATE campaigns c SET house_rules_made = c.house_rules_made + k.moved
        FROM (SELECT guild_id, campaign_id, count(*) AS moved FROM moved_rules
              GROUP BY guild_id, campaign_id) k
        WHERE c.guild_id = k.guild_id AND c.id = k.campaign_id;
    DELETE FROM homebrew_entries h USING moved_rules m WHERE h.id = m.id;
    UPDATE homebrew_entries SET kind = 'other' WHERE kind = 'rule';
    DELETE FROM pack_suggestions WHERE kind = 'rule' AND status = 'open';

    ALTER TABLE homebrew_entries DROP CONSTRAINT homebrew_entries_kind_check;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other'));

    DROP POLICY migrate_backfill ON homebrew_entries;
    DROP POLICY migrate_backfill ON house_rules;
    DROP POLICY migrate_backfill ON campaigns;
    DROP POLICY migrate_backfill ON pack_suggestions;
"""

DM_BOT_CHANNEL = """
    -- The DM Bot channel (docs/PLAN.md, "The DM Bot channel"; #1666): `dmb-dm-bot-<slug>`,
    -- everyone reads, only DMbot posts, with one panel message of buttons by role. Its ids are
    -- a channel id and a message id: they mean nothing in another server, so (like the DM
    -- screen's) none goes into a backup. Nullable columns: no row changes, so no row-level-
    -- security policy is needed.
    ALTER TABLE campaigns ADD COLUMN dm_bot_channel_id BIGINT;
    ALTER TABLE campaigns ADD COLUMN dm_bot_message_id BIGINT;
"""

RULES_CHANNEL = """
    -- The rules archive channel (docs/PLAN.md, "Channels"; #1752): `dmb-rules-<slug>`, a
    -- controlled channel that follows the DM screen's visibility and shows the campaign's house
    -- rules, read only. Like the other channel ids it means nothing in another server, so none
    -- goes into a backup. A nullable column: no row changes, so no row-level-security policy.
    ALTER TABLE campaigns ADD COLUMN rules_channel_id BIGINT;
"""

NPCS_CHANNEL = f"""
    -- The NPC roster channel (docs/PLAN.md, "Channels"; #1761): `dmb-npcs-<slug>`, a controlled
    -- channel that follows the DM screen's visibility and lists the confirmed NPCs and groups the
    -- players may know, read only. Like the other channel ids it means nothing in another server,
    -- so none goes into a backup. A nullable column: no row changes, so no row-level-security
    -- policy.
    ALTER TABLE campaigns ADD COLUMN npcs_channel_id BIGINT;

    -- Which Discord message shows which entry, so the channel's text never carries an id. Message
    -- ids mean nothing in another server, so it is not in a backup either; a lost row is made
    -- good at the next start (DMbot's own messages with no row are deleted and posted again).
    -- `entry_id` is an entry's id, or 'more' for the "+N more" message. New table, no rows.
    CREATE TABLE npcs_channel_messages (
        {_memory_scope()}
        entry_id    TEXT NOT NULL CHECK (length(entry_id) BETWEEN 1 AND 32),
        message_id  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entry_id)
    );
    """ + _isolate("npcs_channel_messages")

INITIATIVE_STYLE = """
    -- How the table runs initiative, as the DM's words last set it (docs/PLAN.md, "FightBot",
    -- "Initiative style, heard"; #1784): `each`, `side`, `reroll`, then `+declare` when actions
    -- are declared first, or `declare` alone. NULL: nothing remembered. A nullable column: no
    -- row changes, so no row-level-security policy. It goes in a backup with the campaign.
    ALTER TABLE campaigns ADD COLUMN initiative_style TEXT
        CHECK (initiative_style IN (
            'each', 'side', 'reroll', 'declare',
            'each+declare', 'side+declare', 'reroll+declare'
        ));
"""

INITIATIVE_STYLE_BACKFILL = """
    -- The retired side-initiative switch (#1127, #1769) said the table plays by side, which is
    -- what the kept initiative style says (#1784). Fold it in, so a DM who turned it on is not
    -- asked to say it again (#1791). A style already kept is left alone. The column stays.
    -- Migrations run with no server set, so row-level security hides every row: this opens
    -- the one table it reads and writes for itself (CLAUDE.md), then closes it again.
    CREATE POLICY migrate_backfill ON campaigns FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaigns SET initiative_style = 'side'
        WHERE side_initiative AND initiative_style IS NULL;
    DROP POLICY migrate_backfill ON campaigns;
"""

RECAP_TO_CONFIRM = """
    -- Names a recap kept that no list had, for the DM to confirm (#2027): shown under the DM's
    -- copy as "Facts to be confirmed" and queued in the name check. names_state: 0 not yet told,
    -- 1 the DM screen was told, 2 and reminded at the next session. Defaulted columns change no
    -- rows, so no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN to_confirm TEXT[] NOT NULL DEFAULT '{}';
    ALTER TABLE recap_jobs ADD COLUMN names_state INTEGER NOT NULL DEFAULT 0
        CHECK (names_state BETWEEN 0 AND 2);
"""

QUIET_STOP = """
    -- Minutes with nobody speaking before DMbot stops a session by itself (#2034): 30, 60 or 90,
    -- or 0 for off. A defaulted column changes no rows, so no row-level-security policy is
    -- needed.
    ALTER TABLE campaigns
        ADD COLUMN quiet_stop_minutes INTEGER NOT NULL DEFAULT 30
            CHECK (quiet_stop_minutes IN (0, 30, 60, 90));
"""

QUIET_STOP_CHOICES_SQL = """
    -- Stop when quiet has no Off and offers 5, 10, 15 or 30 minutes, 10 by default (#2288).
    -- A campaign on Off, 60 or 90 goes to 30, the longest wait left; one on 30 stays on 30.
    -- This changes rows, and every campaign table has forced row-level security, so the update
    -- opens the table for itself and closes it again (migrations run with no server set).
    ALTER TABLE campaigns DROP CONSTRAINT campaigns_quiet_stop_minutes_check;
    CREATE POLICY migrate_backfill ON campaigns USING (true) WITH CHECK (true);
    UPDATE campaigns SET quiet_stop_minutes = 30 WHERE quiet_stop_minutes NOT IN (5, 10, 15, 30);
    DROP POLICY migrate_backfill ON campaigns;
    ALTER TABLE campaigns ALTER COLUMN quiet_stop_minutes SET DEFAULT 10;
    ALTER TABLE campaigns
        ADD CONSTRAINT campaigns_quiet_stop_minutes_check
            CHECK (quiet_stop_minutes IN (5, 10, 15, 30));
"""

RECAP_STYLE = f"""
    -- The examples a DM pastes so recaps sound the way they like (#2075). DMbot copies how an
    -- example sounds, never what happens in it. At most 3 a campaign, each at most 600 words
    -- (the store checks both). Per campaign, isolated, deleted with the campaign, and in the
    -- campaign's backup.
    CREATE TABLE recap_style_examples (
        {_memory_scope()}
        -- Never reused, so a button on an old panel can't remove a newer example (#2075).
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER NOT NULL CHECK (position >= 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """ + _isolate("recap_style_examples")

RECAP_STYLE_NOTES = (
    f"""
    -- The standing notes a DM keeps about how recaps should sound (#2076), sorted out of their
    -- feedback and saved only when they press Save. At most 10 a campaign (the store drops the
    -- oldest). Per campaign, isolated, deleted with the campaign, and in its backup.
    CREATE TABLE recap_style_notes (
        {_memory_scope()}
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER NOT NULL CHECK (position >= 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 300),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("recap_style_notes")
    + """
    -- What the DM said was wrong in one recap or should change in it (#2076): given to the writer
    -- on Try again as facts the DM confirmed, for that recap only. A defaulted column changes no
    -- rows, so no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN corrections TEXT[] NOT NULL DEFAULT '{}';
    """
)
RECAP_APPROVAL = """
    -- The DM approves a recap's text before any audio is made, and picks what to make (#2083).
    -- approved_hash is a fingerprint of the title and summary the DM approved (sha256 of
    -- "title\\nsummary", the audio's own `text_hash`), so an edit after approval makes it stale
    -- with no one having to clear it: an approval counts only while the hash matches the text.
    -- audio_choice: voice, music, both, or text (no audio). Defaulted columns change no rows, so
    -- no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN approved_by BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN approved_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN approved_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE recap_jobs ADD COLUMN audio_choice TEXT NOT NULL DEFAULT ''
        CHECK (audio_choice IN ('', 'voice', 'music', 'both', 'text'));

    -- Approving from the website. The website's role may only change the columns its grant names,
    -- so approving goes through this door: only a DM of the campaign, only a recap that is ready
    -- and still the DM's to post. It records who and when (the database's own clock) and the hash
    -- of the text as it is now, and, for any choice but text, queues the audio the way the bot
    -- does. The page checks the cues first; the secret names and the plan are checked again by the
    -- audio step before a paid call, as for every audio.
    CREATE FUNCTION dmbot_recap_approve(
        g BIGINT, c TEXT, at_ BIGINT, choice TEXT, expected TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF choice NOT IN ('voice', 'music', 'both', 'text') THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' OR job.status <> 'draft' THEN
            RETURN 'not_allowed';
        END IF;
        -- The page checked the cues of one text; only that text is approved. An edit committed
        -- since (a different hash) approves nothing.
        IF expected <> encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                              'hex') THEN
            RETURN 'not_allowed';
        END IF;
        -- While the audio is queued or being made nothing is approved over it, text only
        -- included: the spend so far would be lost and the caps dodged. Audio made from older
        -- text (the DM has edited since) may be made again from the new text.
        IF job.audio = 'queued' THEN
            RETURN 'not_allowed';
        END IF;
        IF choice <> 'text' AND NOT (
            job.audio IN ('off', 'failed')
            OR (job.audio = 'ready' AND job.audio_text_hash <> encode(
                sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex'))
        ) THEN
            RETURN 'not_allowed';
        END IF;
        UPDATE recap_jobs SET
            approved_by = dmbot_current_user(), approved_at = now_s,
            approved_hash = encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                                   'hex'),
            audio_choice = choice,
            audio = CASE WHEN choice = 'text' THEN job.audio ELSE 'queued' END,
            audio_tries = CASE WHEN choice = 'text' THEN job.audio_tries ELSE 0 END,
            audio_next_at = CASE WHEN choice = 'text' THEN job.audio_next_at ELSE 0 END,
            audio_error = CASE WHEN choice = 'text' THEN job.audio_error ELSE '' END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'approved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;

    -- The names the website must check a recap's text against before it approves (#2083): the
    -- campaign's secret names (a secret alias, or any name of an entry limited to some of the
    -- players) and its confirmed names that are not secret and not someone at the table, so
    -- "Arcane Brotherhood" inside the confirmed "Arcane Brotherhood Hall" is read the way the bot
    -- reads it, and who is recorded in the server under the current terms and the outside engine
    -- in use, as the bot's consent check reads it (the website's role sees only a person's own
    -- consent).
    -- Only a DM of the campaign, in a server on the session's list. The API uses them to
    -- check one text and never returns them: the website's role has no grant on the memory tables.
    CREATE FUNCTION dmbot_recap_check_names(g BIGINT, c TEXT, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND (a.secret OR e.audience_limited)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND NOT e.audience_limited
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb),
            'recorded', COALESCE((
                SELECT jsonb_agg(c2.user_id) FROM consent c2
                WHERE c2.guild_id = g AND c2.terms_version = terms
                  AND (outside IS NULL OR c2.outside_to = outside)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;

    -- "Make the audio again" needs a current approval (#2083): the audio is only ever made for a
    -- text the DM approved with a choice that makes audio, and it is made as they chose. The same
    -- door as before (#1418) with that one condition more.
    CREATE OR REPLACE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' OR job.status NOT IN ('draft', 'posting')
           OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_choice NOT IN ('voice', 'music', 'both')
           OR job.approved_hash <> encode(
               sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex') THEN
            RETURN 'needs_approval';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

RECAP_AUDIO_AGAIN_POSTED = """
    -- "Make it again" for a POSTED recap whose narration no longer matches its text, for example
    -- after a spelling fix (#2183, #2177). The same door as before (#1418, #2083) with one case
    -- more: a recap already posted, whose audio is made (ready) from other words than the text now
    -- (audio_text_hash differs). Everything else is as before: only a DM of the campaign, a text
    -- the DM approved with a choice that makes audio, once every 10 minutes (the database's own
    -- clock). The plan and the month's allowance are checked by the API and again by the audio
    -- step before any paid call. The Discord message already posted keeps its old attachment.
    -- A posted recap whose remake failed may be tried again: a failed making keeps the hash of the
    -- last audio that was ready, which is still not the text's hash now.
    -- A new making starts its own credit count: the cap is for one making, not for all of them.
    CREATE OR REPLACE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        now_hash TEXT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' THEN
            RETURN 'not_allowed';
        END IF;
        now_hash := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        IF job.status = 'posted' THEN
            -- Only audio that was made and no longer matches the text.
            IF job.audio NOT IN ('ready', 'failed') OR job.audio_text_hash = now_hash THEN
                RETURN 'not_allowed';
            END IF;
        ELSIF job.status NOT IN ('draft', 'posting') OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_choice NOT IN ('voice', 'music', 'both') OR job.approved_hash <> now_hash THEN
            RETURN 'needs_approval';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s,
                              audio_credits = CASE WHEN job.audio = 'ready'
                                                   THEN 0 ELSE audio_credits END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    -- Before this, a recap posted while its audio was still queued was left alone: posted, audio
    -- 'queued', nothing ever made. The audio step now takes posted recaps, so those would be
    -- narrated after this deploy with nobody asking, mostly charged to past months. They are
    -- marked failed instead; the DM may ask again from the recap page. Migrations run with no
    -- server set, so the table is opened to this one UPDATE and closed again.
    CREATE POLICY migrate_backfill ON recap_jobs FOR ALL USING (true) WITH CHECK (true);
    UPDATE recap_jobs SET audio = 'failed',
        audio_error = 'The audio wasn''t made before this recap was posted. '
                      'You can make it now for this page.'
        WHERE status = 'posted' AND audio = 'queued';
    DROP POLICY migrate_backfill ON recap_jobs;
"""

STORY_MODE = """
    -- The Shared story switch (docs/STORY_MEMORY.md, section 6; #1692): Off (the default),
    -- Watch the story or Guide me. Nothing reads it yet: the story-plan reader, the divergence
    -- warnings and Guide me are the next slices. A defaulted column changes no rows, so no
    -- row-level-security policy is needed. The DM's confirmation of the right to use the
    -- material is a row in shared_confirmations (purpose 'shared_story'), not a column.
    ALTER TABLE campaigns
        ADD COLUMN story_mode TEXT NOT NULL DEFAULT 'off'
            CHECK (story_mode IN ('off', 'watch', 'guide'));
"""

ADVENTURE_TEXT = (
    f"""
    -- The DM's adventure text (docs/STORY_MEMORY.md, section 6, "Where the story plan comes from";
    -- #1698; CLAUDE.md, IP rule): a file the DM gave DMbot for one campaign, split into chunks so a
    -- later slice (the story-plan reader) can use it. Nothing reads it yet. The text stays in
    -- its campaign: row-level security is forced, both tables are deleted with the campaign
    -- (ON DELETE CASCADE) and a document's chunks go with the document. `fingerprint` is the
    -- SHA-256 of the text, so the same file twice is refused. New tables, no existing rows.
    CREATE TABLE adventure_documents (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        file_name   TEXT NOT NULL CHECK (char_length(file_name) BETWEEN 1 AND 200),
        fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        characters  INTEGER NOT NULL CHECK (characters > 0),
        chunks      INTEGER NOT NULL CHECK (chunks >= 0),  -- 0: restored from a backup, no text
        added_by    BIGINT NOT NULL CHECK (added_by > 0),
        added_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, fingerprint)
    );
    CREATE TABLE adventure_chunks (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        position    INTEGER NOT NULL CHECK (position >= 0),
        heading     TEXT NOT NULL CHECK (char_length(heading) <= 200),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4000),
        created_at  BIGINT NOT NULL,
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id, position),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """
    + _isolate("adventure_documents")
    + _isolate("adventure_chunks")
)

STORY_PLAN = (
    f"""
    -- The story plan (docs/STORY_MEMORY.md, section 6; #1714): what the DM's written adventure
    -- expects, read from its text by the AI once, when the DM asks. A layer of its own, never
    -- mixed with the table's facts (memory_*) and never shown to players. Every table is
    -- isolated per server and campaign. The nodes, links and read records are deleted with the
    -- document that produced them (ON DELETE CASCADE) and with the campaign, and are NOT in
    -- backups (the text isn't either): after a restore the file is given again and read again.
    -- `plan_spend` is the exception to "goes with the document": it is a ledger of what reading
    -- cost, kept by the file's fingerprint (the SHA-256 of its text), so removing a file and
    -- giving it again can't get round the caps. A row is a reservation of the most a call could
    -- cost until the call returns, then its real cost. It goes with the campaign. New tables, no
    -- existing rows.
    CREATE TABLE adventure_reads (
        {_memory_scope()}
        document_id TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('reading', 'done', 'stopped')),
        next_chunk  INTEGER NOT NULL CHECK (next_chunk >= 0),
        total_chunks INTEGER NOT NULL CHECK (total_chunks > 0),
        stop_reason TEXT NOT NULL DEFAULT '' CHECK (char_length(stop_reason) <= 40),
        skipped_parts INTEGER NOT NULL DEFAULT 0 CHECK (skipped_parts >= 0),
        started_by  BIGINT NOT NULL CHECK (started_by > 0),
        started_at  BIGINT NOT NULL,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, document_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE plan_nodes (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('chapter', 'place', 'person')),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
        name_key    TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 120),
        summary     TEXT NOT NULL CHECK (char_length(summary) <= 500),
        quote       TEXT NOT NULL CHECK (char_length(quote) <= 200),
        chunk_ids   TEXT[] NOT NULL CHECK (cardinality(chunk_ids) <= 8),
        position    INTEGER NOT NULL CHECK (position >= 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id, kind, name_key),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    -- "From needs to": Chapter 3 needs Captain Varn (alive). `need` is what the book expects of
    -- the second end, in a few words, or empty.
    CREATE TABLE plan_links (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        from_id     TEXT NOT NULL,
        to_id       TEXT NOT NULL,
        need        TEXT NOT NULL CHECK (char_length(need) <= 120),
        quote       TEXT NOT NULL CHECK (char_length(quote) <= 200),
        chunk_ids   TEXT[] NOT NULL CHECK (cardinality(chunk_ids) <= 8),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, from_id, to_id, need),
        CHECK (from_id <> to_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, from_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, to_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE plan_spend (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        month       TEXT NOT NULL CHECK (month ~ '^[0-9]{{4}}-[0-9]{{2}}$'),
        micro_usd   BIGINT NOT NULL CHECK (micro_usd >= 0),
        input_tokens  INTEGER NOT NULL CHECK (input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
        at          BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX plan_spend_month ON plan_spend (guild_id, campaign_id, month);
    CREATE INDEX plan_nodes_document ON plan_nodes (guild_id, campaign_id, document_id, kind);
    """
    + _isolate("adventure_reads")
    + _isolate("plan_nodes")
    + _isolate("plan_links")
    + _isolate("plan_spend")
)

PLAN_SAME_AS = f"""
    -- The DM's word that an entry in their adventure and an entry in their table's story are one
    -- and the same (docs/STORY_MEMORY.md, section 6; #1715): "the book's Captain Varn" is "our
    -- Captain Varn". Or that they are not, remembered so it isn't asked again. Only ever written
    -- by the DM's press; DMbot suggests by name (no AI) and never decides. Secret by being the
    -- DM's alone: no recall, transcript or player view reads this table. Logged in the memory
    -- change log (an Undo works) and gone with the plan entry or the table entry. Not in backups,
    -- like the plan. `document_id` is the plan entry's file, kept here for the one-to-one rule.
    CREATE TABLE plan_same_as (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        plan_id     TEXT NOT NULL,
        document_id TEXT NOT NULL,
        entity_id   TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('same', 'not_same')),
        decided_by  BIGINT NOT NULL CHECK (decided_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, plan_id, entity_id),
        FOREIGN KEY (guild_id, campaign_id, plan_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE,
        {_entity_link("entity_id")}
    );
    -- A book entry is the same as at most one table entry, and a table entry as at most one
    -- book entry in each file.
    CREATE UNIQUE INDEX plan_same_as_one_for_the_book
        ON plan_same_as (guild_id, campaign_id, plan_id) WHERE status = 'same';
    CREATE UNIQUE INDEX plan_same_as_one_for_the_table
        ON plan_same_as (guild_id, campaign_id, entity_id, document_id) WHERE status = 'same';
    """ + _isolate("plan_same_as")

PLAN_DIVERGENCES = f"""
    -- Where the DM's table has left the DM's adventure (docs/STORY_MEMORY.md, section 6; #1729):
    -- the adventure says "Chapter 3 needs Captain Varn alive", the DM said the book's Varn is the
    -- table's Varn, and the table says he is dead. One row for each such clash and each
    -- condition it saw, so the DM is told once. `state_id` is the table's condition row at the
    -- time; it has no foreign key because an Undo deletes that row, and the next check marks the
    -- clash 'gone' (so does a condition that changed again, or a book link taken off). `open` was
    -- shown, `known` is the DM's "I know". Derived from the plan and the table, so it is gone with
    -- the plan link or the table entry, and is NOT in backups, like the plan. New table, no
    -- existing rows.
    CREATE TABLE plan_divergences (
        {_memory_scope()}
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        plan_link_id TEXT NOT NULL,
        entity_id    TEXT NOT NULL,
        state_id     TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('open', 'known', 'gone')),
        created_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, plan_link_id, state_id),
        FOREIGN KEY (guild_id, campaign_id, plan_link_id)
            REFERENCES plan_links (guild_id, campaign_id, id) ON DELETE CASCADE,
        {_entity_link("entity_id")}
    );
    -- (The two indexes below are for the cascades, so deleting a plan entry or a table entry
    -- doesn't scan the other tables; the check itself is served by the unique keys.)
    CREATE INDEX plan_links_to ON plan_links (guild_id, campaign_id, to_id);
    CREATE INDEX plan_same_as_entity ON plan_same_as (guild_id, campaign_id, entity_id);
    """ + _isolate("plan_divergences")

PLAN_POSITION = f"""
    -- Where the DM says the table is in their adventure (docs/STORY_MEMORY.md, section 6; #1731,
    -- Guide me): one chapter for each file, the DM's word, which beats DMbot's guess from the
    -- names heard in the last two sessions until the DM clears it. Only ever written by the DM's
    -- press; logged in the memory change log (an Undo works). Gone with its file, its chapter and
    -- the campaign, and NOT in backups, like the plan. New table, no existing rows.
    CREATE TABLE plan_position (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        document_id TEXT NOT NULL,
        chapter_id  TEXT NOT NULL,
        set_by      BIGINT NOT NULL CHECK (set_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, document_id),
        FOREIGN KEY (guild_id, campaign_id, document_id)
            REFERENCES adventure_documents (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, chapter_id)
            REFERENCES plan_nodes (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """ + _isolate("plan_position")

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

RECAP_AGAIN = r"""
    -- A DM makes or redoes the recap of any past session (#2608).
    --   restarted_at  when the DM last started this recap over (Unix seconds); the month's
    --                 allowance counts a restarted recap in the month it was restarted.
    --   previous      the recap that was approved or posted before the DM started over, kept
    --                 until the new one is approved (words, approval and where it was posted).
    --                 Nothing is read from it once the new text is approved or posted.
    ALTER TABLE recap_jobs ADD COLUMN restarted_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN previous JSONB;
    -- The one door that starts a recap over. It is for the bot, which has checked nothing but
    -- that `who` is the person asking; it checks that `who` is a DM of the campaign, that the
    -- session still has its lines, and that nothing is being written for it now. Not granted to
    -- the website: its door (`dmbot_recap_redo`) passes the signed-in person.
    CREATE FUNCTION dmbot_recap_start_over(
        g BIGINT, c TEXT, at_ BIGINT, who BIGINT, replace_ok BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        keep JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g OR who IS NULL OR who <= 0
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN 'not_allowed';
        END IF;
        -- The session's words must still exist: retention may have deleted them.
        IF NOT EXISTS (
            SELECT 1 FROM transcript_sessions s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
              AND EXISTS (SELECT 1 FROM transcript_lines l
                          WHERE l.guild_id = g AND l.session_id = s.id AND l.sidebar IS NULL)
        ) THEN
            RETURN 'no_lines';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, created_at,
                                    updated_at, restarted_at)
                VALUES (g, c, at_, who, now_s, now_s, now_s);
            RETURN 'queued';
        END IF;
        -- Going out, or a worker has it right now: nothing to start over until that ends. A recap
        -- that waits (a held one, or one between tries) is startable: that is the DM's way out.
        IF job.status = 'posting' OR job.leased_to > now_s THEN
            RETURN 'busy';
        END IF;
        IF job.state = 'ready' AND job.status IN ('draft', 'posted') AND NOT replace_ok THEN
            RETURN 'needs_confirm';
        END IF;
        keep := NULL;
        IF job.state = 'ready' AND (job.status = 'posted' OR job.approved_hash <> '') THEN
            keep := jsonb_build_object(
                'title', job.title, 'summary', job.summary, 'original', job.original,
                'housekeeping', to_jsonb(job.housekeeping), 'music', to_jsonb(job.music),
                'game_day', job.game_day, 'status', job.status,
                'approved_by', job.approved_by, 'approved_at', job.approved_at,
                'approved_hash', job.approved_hash, 'audio_choice', job.audio_choice,
                'speakers', to_jsonb(job.speakers), 'grade', job.grade,
                'posted_url', job.posted_url, 'decided_at', job.decided_at);
        ELSIF job.previous IS NOT NULL AND job.approved_hash = '' AND job.status = 'draft' THEN
            keep := job.previous;  -- started over twice before the new one was approved
        END IF;
        UPDATE recap_jobs SET
            state = 'queued', attempt = 0, errors = 0, restarts = 0, next_at = 0, leased_to = 0,
            calls = 0, window_count = 0, window_done = 0, last_error = '', problems = '{}',
            beats = '[]', title = '', summary = '', original = '', housekeeping = '{}',
            music = '{}', game_day = NULL, status = 'draft', tries = 0, speakers = '{}',
            grade = '{}', emailed_at = NULL, decided_at = NULL, to_confirm = '{}',
            names_state = 0, corrections = '{}', approved_by = 0, approved_at = 0,
            approved_hash = '', audio_choice = '', split = NULL, critics = NULL,
            audio = 'off', audio_tries = 0, audio_next_at = 0, audio_text_hash = '',
            audio_credits = 0, audio_error = '', audio_asked_at = 0, posting_since = NULL,
            posting_step = NULL, posted_url = NULL, respelled_at = NULL,
            dm_user_id = who, updated_at = now_s, previous = keep,
            -- (only a recap the DM chose to redo counts again in the month: a failed or held one
            -- was never counted, and its redo is the same recap)
            restarted_at = CASE WHEN job.state = 'ready' THEN now_s ELSE job.restarted_at END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    -- The website's door: the signed-in person, a DM of the campaign, in a server on their list.
    CREATE FUNCTION dmbot_recap_redo(g BIGINT, c TEXT, at_ BIGINT, replace_ok BOOLEAN)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        RETURN dmbot_recap_start_over(g, c, at_, dmbot_current_user(), replace_ok);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_start_over(BIGINT, TEXT, BIGINT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_start_over(BIGINT, TEXT, BIGINT, BIGINT, BOOLEAN)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN) FROM PUBLIC;
"""

RECAP_UPLOADED = (
    r"""
    -- A recap written by someone at the table, kept as a session's recap (#2609).
    --   written_by   who wrote it, as the DM typed it; empty for a recap DMbot wrote
    --   uploaded_by  the DM who added it, and uploaded_at when (Unix seconds)
    --   earlier      the recaps it replaced, newest first, as {title, summary, written_by,
    --                posted_url, replaced_at}: never silently overwritten
    --   checked_at   when its names were checked (0: not yet); the bot does that next round
    -- The text columns hold up to 50,000 characters (a length is the writer's choice, #2597).
    -- The constraints are replaced; no row is rewritten, so no row-level security is touched.
    ALTER TABLE recap_jobs
        DROP CONSTRAINT recap_jobs_summary_check,
        DROP CONSTRAINT recap_jobs_original_check,
        ADD CONSTRAINT recap_jobs_summary_check CHECK (char_length(summary) <= 50000),
        ADD CONSTRAINT recap_jobs_original_check CHECK (char_length(original) <= 50000),
        ADD COLUMN written_by  TEXT NOT NULL DEFAULT '' CHECK (char_length(written_by) <= 80),
        ADD COLUMN uploaded_by BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN uploaded_at BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN earlier     JSONB NOT NULL DEFAULT '[]',
        ADD COLUMN checked_at  BIGINT NOT NULL DEFAULT 0;
    -- The first upload of a campaign asks for one confirmation, kept with who and when, as for
    -- other shared content: "written by someone at your table, and you have their OK".
    CREATE TABLE recap_upload_rights (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        confirmed_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("recap_upload_rights")
    + r"""
    -- The one door that stores an uploaded recap. For the bot; the website's door passes the
    -- signed-in person. It checks that `who` is a DM of the campaign, that the right to use was
    -- confirmed (now, or before), that nothing is being written for the session, and that an
    -- existing recap is replaced only with the DM's yes, keeping the old one in `earlier`.
    CREATE FUNCTION dmbot_recap_add_uploaded(
        g BIGINT, c TEXT, at_ BIGINT, who BIGINT, ttl TEXT, body TEXT, author TEXT,
        replace_ok BOOLEAN, rights BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        kept JSONB;
        digest TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g OR who IS NULL OR who <= 0 OR at_ IS NULL
           OR at_ <= 0
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN 'not_allowed';
        END IF;
        IF char_length(btrim(body)) = 0 OR char_length(body) > 50000
           OR char_length(btrim(ttl)) = 0 OR char_length(ttl) > 120
           OR char_length(btrim(author)) = 0 OR char_length(author) > 80 THEN
            RETURN 'bad_text';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_upload_rights r
                       WHERE r.guild_id = g AND r.campaign_id = c) THEN
            IF NOT rights THEN
                RETURN 'needs_rights';
            END IF;
            INSERT INTO recap_upload_rights (guild_id, campaign_id, user_id, confirmed_at)
                VALUES (g, c, who, now_s) ON CONFLICT DO NOTHING;
        END IF;
        digest := encode(sha256(convert_to(ttl || E'\n' || body, 'UTF8')), 'hex');
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state, title,
                                    summary, original, status, created_at, updated_at, emailed_at,
                                    written_by, uploaded_by, uploaded_at, approved_by,
                                    approved_at, approved_hash)
                VALUES (g, c, at_, who, 'ready', ttl, body, body, 'draft', now_s, now_s, now_s,
                        author, who, now_s, who, now_s, digest);
            -- an uploaded recap is approved by being added: it is an accepted version like any
            -- other, so PlotBot's notes and recall see it (#2122, one place for both ways)
            PERFORM dmbot_recap_keep_version(g, c, at_, who, now_s);
            RETURN 'stored';
        END IF;
        -- Going out, or a worker has it right now. A recap that waits is the DM's to replace.
        IF job.status = 'posting' OR job.leased_to > now_s THEN
            RETURN 'busy';
        END IF;
        IF job.state = 'ready' AND job.summary <> '' AND NOT replace_ok THEN
            RETURN 'needs_confirm';
        END IF;
        kept := job.earlier;
        IF job.state = 'ready' AND job.summary <> '' THEN
            kept := (jsonb_build_array(jsonb_build_object(
                'title', job.title, 'summary', job.summary, 'written_by', job.written_by,
                'posted_url', job.posted_url, 'status', job.status, 'replaced_at', now_s))
                || job.earlier);
            -- the ten newest earlier versions
            kept := COALESCE((SELECT jsonb_agg(e) FROM (
                SELECT e FROM jsonb_array_elements(kept) WITH ORDINALITY AS t(e, n)
                ORDER BY n LIMIT 10) q), '[]'::jsonb);
        END IF;
        UPDATE recap_jobs SET
            state = 'ready', attempt = 0, errors = 0, restarts = 0, next_at = 0, leased_to = 0,
            calls = 0, window_count = 0, window_done = 0, last_error = '', problems = '{}',
            beats = '[]', title = ttl, summary = body, original = body, housekeeping = '{}',
            music = '{}', game_day = NULL, status = 'draft', tries = 0, speakers = '{}',
            grade = '{}', emailed_at = now_s, decided_at = NULL, to_confirm = '{}',
            names_state = 0, corrections = '{}', approved_by = who, approved_at = now_s,
            approved_hash = digest, audio_choice = '', split = NULL, critics = NULL,
            audio = 'off', audio_tries = 0, audio_next_at = 0, audio_text_hash = '',
            audio_credits = 0, audio_error = '', audio_asked_at = 0, posting_since = NULL,
            posting_step = NULL, posted_url = NULL, respelled_at = NULL, previous = NULL,
            written_by = author, uploaded_by = who, uploaded_at = now_s, earlier = kept,
            checked_at = 0, dm_user_id = who, updated_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        PERFORM dmbot_recap_keep_version(g, c, at_, who, now_s);
        RETURN 'stored';
    END
    $fn$;
    CREATE FUNCTION dmbot_recap_upload(
        g BIGINT, c TEXT, at_ BIGINT, ttl TEXT, body TEXT, author TEXT, replace_ok BOOLEAN,
        rights BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        RETURN dmbot_recap_add_uploaded(g, c, at_, dmbot_current_user(), ttl, body, author,
                                        replace_ok, rights);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_add_uploaded(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT,'
            ' TEXT, BOOLEAN, BOOLEAN) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_upload(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN,'
            ' BOOLEAN) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_add_uploaded(
        BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_upload(
        BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN) FROM PUBLIC;
    """
)

TRANSCRIPT_ERASURES = (
    """
    -- Who erased their transcripts in a server, and when (#2605). A backup made before that is
    -- restored without those lines: the person's "Delete my transcripts" must hold for a copy
    -- too. One row for each person and server (a later erase replaces the time). Ids and a time
    -- only, no words. A restore into another server cannot see this server's rows, so it asks
    -- through a door that answers for one server and the speakers named, and says nothing else.
    CREATE TABLE transcript_erasures (
        guild_id  BIGINT NOT NULL,
        user_id   BIGINT NOT NULL,
        erased_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("transcript_erasures")
    + """
    CREATE FUNCTION dmbot_erased_speakers(g BIGINT, users BIGINT[])
        RETURNS TABLE (user_id BIGINT, erased_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.guild_id', true), '');
    BEGIN
        -- (the table's rows are isolated by server even for its owner: look as that server,
        -- for this one query, then as the caller's again)
        PERFORM set_config('dmbot.guild_id', g::TEXT, true);
        RETURN QUERY SELECT e.user_id, e.erased_at FROM transcript_erasures e
            WHERE e.guild_id = g AND e.user_id = ANY(users);
        PERFORM set_config('dmbot.guild_id', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_erased_speakers(BIGINT, BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_erased_speakers(BIGINT, BIGINT[]) FROM PUBLIC;
"""
)

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

EMAIL_CHANGES = """
    -- A waiting change of the email DMbot uses (#2349): one row per account (a new ask replaces the
    -- last one), holding the address asked for and only the SHA-256 of the confirm link's token,
    -- like a session cookie. Own row only; the link works for the account that asked.
    CREATE TABLE email_changes (
        user_id     BIGINT PRIMARY KEY REFERENCES web_users ON DELETE CASCADE,
        new_email   TEXT NOT NULL,
        token_hash  TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL
    );
    -- When the person chose their address on the account page: a Discord sign-in keeps it from
    -- then on instead of putting Discord's address back (no existing row changes).
    ALTER TABLE web_users ADD COLUMN email_chosen_at BIGINT;
    ALTER TABLE email_changes ENABLE ROW LEVEL SECURITY;
    ALTER TABLE email_changes FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON email_changes
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());

    -- Whether another account already uses an address (sign-in by email needs one address per
    -- account). The person is the one signed in; the function runs with its owner's rights to
    -- read the other rows, opened by the same setting and policy as dmbot_user_by_email, and
    -- answers only yes or no.
    CREATE FUNCTION dmbot_email_taken(addr TEXT) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found BOOLEAN;
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.email_lookup', true), '');
    BEGIN
        IF me IS NULL OR addr IS NULL OR addr = '' THEN
            RETURN NULL;
        END IF;
        -- Two accounts confirming the same address at once would both pass the check below: one
        -- lock per address, held until the caller's transaction ends, makes the second one wait
        -- and then see the first one's row.
        PERFORM pg_advisory_xact_lock(hashtextextended(lower(addr), 2349));
        PERFORM set_config('dmbot.email_lookup', 'on', true);
        SELECT EXISTS (
            SELECT 1 FROM web_users
            WHERE user_id <> me
              AND (lower(email) = lower(addr) OR lower(payment_email) = lower(addr))
        ) INTO found;
        PERFORM set_config('dmbot.email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_email_taken(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_email_taken(TEXT) FROM PUBLIC;
"""

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
    # Whether another account has an address, for the signed-in person's change of email (#2349).
    ("dmbot_email_taken", "dmbot_email_taken(TEXT)"),
    # Experience points (#1228): the one door to the person's all-time minutes.
    ("dmbot_minutes_total", "dmbot_minutes_total()"),
    ("dmbot_user_of", "dmbot_user_of(TEXT)"),  # links made before a move (#1230)
    ("dmbot_moved_to", "dmbot_moved_to(BIGINT, TEXT, TEXT)"),  # payments after a move (#1230)
    ("dmbot_moves_to_sync", "dmbot_moves_to_sync()"),  # the job that updates Stripe (#1230)
    ("dmbot_move_synced", "dmbot_move_synced(BIGINT)"),
    ("dmbot_move_failed", "dmbot_move_failed(BIGINT)"),
    # Add-on subscriptions that must be cancelled, kept until the company says yes (#1399).
    ("dmbot_note_stray_addon", "dmbot_note_stray_addon(TEXT)"),
    ("dmbot_strays_to_cancel", "dmbot_strays_to_cancel()"),
    ("dmbot_stray_addon_tried", "dmbot_stray_addon_tried(TEXT)"),
    ("dmbot_stray_addon_done", "dmbot_stray_addon_done(TEXT)"),
    ("dmbot_previous_ids", "dmbot_previous_ids()"),  # the export's earlier Discord accounts
    ("dmbot_forget_account_history", "dmbot_forget_account_history()"),  # account deletion
    ("dmbot_set_payment_email", "dmbot_set_payment_email(TEXT, TEXT, BIGINT)"),  # #1242
    ("dmbot_forget_email_records", "dmbot_forget_email_records()"),  # account deletion, #1287
    # The admin page reads a ticket's reply address, and only while its door is open (#1340).
    ("dmbot_support_email", "dmbot_support_email(BIGINT)"),
    # ... and its words, the same way (#1373): the website's role has no right to the column.
    ("dmbot_support_message", "dmbot_support_message(BIGINT)"),
    # "Make the audio again" on a DM's recap (#1418): the only write to its audio columns.
    ("dmbot_recap_audio_again", "dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)"),
    ("dmbot_recap_redo", "dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN)"),  # #2608
    # A recap written by someone at the table, added by a DM on the page (#2609).
    (
        "dmbot_recap_upload",
        "dmbot_recap_upload(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN)",
    ),
    # Importing older sessions (#2105): each is held to a DM of the campaign.
    ("dmbot_import_agreed", "dmbot_import_agreed(BIGINT, TEXT, INT, TEXT)"),
    (
        "dmbot_import_store",
        "dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT)",
    ),
    ("dmbot_import_members", "dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)"),
    ("dmbot_import_suggest", "dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT)"),
    ("dmbot_import_plays", "dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT)"),
    ("dmbot_import_progress", "dmbot_import_progress(BIGINT, TEXT, TEXT)"),
    ("dmbot_import_mapping", "dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT)"),
    # A posted recap's public link (#2118): turned on and off by a DM, read by anyone with it.
    ("dmbot_recap_share", "dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    ("dmbot_recap_shared", "dmbot_recap_shared(TEXT)"),
    # Approving a recap's text and picking its audio (#2083): the only write to those columns.
    ("dmbot_recap_approve", "dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    # PlotBot's check of a recap: read and answered by a DM (#2122).
    ("dmbot_plot_read", "dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT)"),
    ("dmbot_plot_answer", "dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)"),
    # The DM's talk with PlotBot about an issue, and Save / Not that on its proposals (#2123).
    ("dmbot_plot_chat_send", "dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    ("dmbot_plot_chat_read", "dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT)"),
    ("dmbot_plot_proposal", "dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)"),
    # ... and approving a draft that waited 3 days for its check, as it is (#2305).
    (
        "dmbot_recap_approve_unchecked",
        "dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)",
    ),
    # How many hand-over emails a person sent today, for the limit of five (#2532).
    ("dmbot_handover_emails_since", "dmbot_handover_emails_since(BIGINT, BIGINT)"),
    ("dmbot_handover_open", "dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN)"),  # #2532
    ("dmbot_handover_release", "dmbot_handover_release(BIGINT, BIGINT, TEXT)"),  # #2532
    (
        "dmbot_handover_request_move",
        "dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)",
    ),  # #2532, slice C (granted now that the bot's move exists)
    ("dmbot_handover_decline", "dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT)"),  # #2532
    ("dmbot_handover_shares_rules", "dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT)"),  # #2532
    ("dmbot_handover_move_failed", "dmbot_handover_move_failed(BIGINT, BIGINT, TEXT)"),  # #2532
    # The owner's own campaigns in any server, and deleting one from the website (#2554).
    ("dmbot_web_owned_campaigns", "dmbot_web_owned_campaigns()"),
    ("dmbot_web_delete_campaign", "dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)"),
    # The copy of a campaign the bot builds and the website downloads (#2554, part 1b).
    ("dmbot_web_backup_add", "dmbot_web_backup_add(BIGINT, TEXT)"),
    ("dmbot_web_backup_read", "dmbot_web_backup_read(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_backup_take", "dmbot_web_backup_take(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_backup_take_part", "dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT)"),
    # Notes typed on the recap page and "Rewrite with these" (#2113).
    ("dmbot_recap_request_add", "dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)"),
    ("dmbot_recap_request_read", "dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT)"),
    (
        "dmbot_recap_rewrite",
        "dmbot_recap_rewrite(BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[])",
    ),
    # The recap's names to check, answered on its page (#2113): the read door and the queue door.
    ("dmbot_recap_names_read", "dmbot_recap_names_read(BIGINT, TEXT, BIGINT)"),
    ("dmbot_recap_name_places", "dmbot_recap_name_places(BIGINT, TEXT, BIGINT)"),
    # One session's after-session report, the DM's alone (#2661).
    ("dmbot_web_report", "dmbot_web_report(BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_report_answer", "dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
    # A campaign's game masters, and the people who can be offered as one more (#2768).
    ("dmbot_web_co_dm_add", "dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT)"),
    ("dmbot_web_game_masters", "dmbot_web_game_masters(BIGINT, TEXT)"),
    (
        "dmbot_web_game_master_candidates",
        "dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT)",
    ),
    ("dmbot_recap_names_fixed", "dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT)"),  # (#2684)
    ("dmbot_recap_present", "dmbot_recap_present(BIGINT, TEXT, BIGINT)"),  # (#2684, #2577)
    (
        "dmbot_recap_set_present",
        "dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)",
    ),
    (
        "dmbot_name_answer_add",
        "dmbot_name_answer_add(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)",
    ),
    # A spoken note: the clip is held until the bot has written it down (#2113).
    (
        "dmbot_recap_voice_add",
        "dmbot_recap_voice_add(BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT)",
    ),
    ("dmbot_recap_voice_read", "dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT)"),
    (
        "dmbot_recap_check_names",
        "dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)",
    ),  # its secret check
    ("dmbot_support_thread", "dmbot_support_thread(BIGINT)"),  # ... and a thread's (#1431)
    # A person's own latest answer, on their own ticket only (#1432).
    ("dmbot_support_latest_answer", "dmbot_support_latest_answer(BIGINT)"),
    # A person's own whole conversation, on their own ticket only (#2193).
    ("dmbot_support_mine_thread", "dmbot_support_mine_thread(BIGINT)"),
    # The DM's recap choices, read and written on the recap page (#1468): DM only.
    ("dmbot_web_recap_choices", "dmbot_web_recap_choices(BIGINT, TEXT)"),
    # The signed-in DM's recaps still waiting for their name answers (#2174).
    ("dmbot_web_recap_holds", "dmbot_web_recap_holds(BIGINT)"),
    # The "Write recaps" choice of one imported session (#2106).
    ("dmbot_import_write_recap", "dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN)"),
    ("dmbot_web_set_recap_choice", "dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN)"),
    # A campaign's house rules, read by any member of the server (#1521).
    ("dmbot_web_house_rules", "dmbot_web_house_rules(BIGINT, TEXT)"),
    ("dmbot_web_house_rules_campaigns", "dmbot_web_house_rules_campaigns(BIGINT)"),
    ("dmbot_web_homebrew", "dmbot_web_homebrew(BIGINT, TEXT)"),
    ("dmbot_web_homebrew_campaigns", "dmbot_web_homebrew_campaigns(BIGINT)"),
    # An NPC's page and a person's own private notes on it, for any member of the server (#2265).
    ("dmbot_web_npc_page", "dmbot_web_npc_page(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_peek", "dmbot_web_peek(BIGINT, TEXT, TEXT)"),
    ("dmbot_recap_held_names", "dmbot_recap_held_names(BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_npc_note_put", "dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_npc_notes_mine", "dmbot_web_npc_notes_mine(BIGINT, TEXT)"),
    ("dmbot_web_npc_notes_delete", "dmbot_web_npc_notes_delete(BIGINT, TEXT)"),
    # A campaign's own page on the website: its name, for any member of the server (#2416).
    ("dmbot_web_campaign_card", "dmbot_web_campaign_card(BIGINT, TEXT)"),
    # The facts on each campaign's card on My Account (#2460).
    ("dmbot_web_campaign_facts", "dmbot_web_campaign_facts(BIGINT)"),
    # The Shared rules page (#2460).
    ("dmbot_web_shared_rules", "dmbot_web_shared_rules(BIGINT)"),
    ("dmbot_web_rule_sharing", "dmbot_web_rule_sharing(BIGINT, TEXT)"),  # (#2783)
    ("dmbot_web_rule_action_add", "dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_rule_action_read", "dmbot_web_rule_action_read(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_report_make", "dmbot_web_report_make(BIGINT, TEXT, BIGINT)"),  # (#2661)
    ("dmbot_web_report_request", "dmbot_web_report_request(BIGINT, TEXT, BIGINT)"),
    # The character sheets page (#2816): what a person may see, and the queue for a link, a forget
    # and a DM's read again.
    ("dmbot_web_sheet_read", "dmbot_web_sheet_read(BIGINT, TEXT)"),
    ("dmbot_web_sheet_action_add", "dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_sheet_action_read", "dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)"),
    # The Story timeline page (#2462).
    ("dmbot_web_story_timeline", "dmbot_web_story_timeline(BIGINT, TEXT)"),
    # A campaign's session transcripts, read by any member of the server (#1501).
    ("dmbot_web_transcript_sessions", "dmbot_web_transcript_sessions(BIGINT, TEXT)"),
    (
        "dmbot_web_transcript_lines",
        "dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)",
    ),
    # Who plays which character in a campaign, for the transcript's labels (#1512).
    ("dmbot_web_transcript_characters", "dmbot_web_transcript_characters(BIGINT, TEXT)"),
    ("dmbot_web_transcript_speakers", "dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT)"),
    # What was left out of a session, and a DM putting it back (#2567).
    (
        "dmbot_web_transcript_stretch",
        "dmbot_web_transcript_stretch(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)",
    ),
    ("dmbot_web_transcript_choices", "dmbot_web_transcript_choices(BIGINT, TEXT, TEXT)"),
    (
        "dmbot_web_transcript_choose",
        "dmbot_web_transcript_choose(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN)",
    ),
    ("dmbot_web_transcript_campaigns", "dmbot_web_transcript_campaigns(BIGINT)"),
    # The campaign's names with the narrator's spelling (#1451): read and write, DM only.
    ("dmbot_web_names", "dmbot_web_names(BIGINT, TEXT)"),
    ("dmbot_web_set_said_as", "dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT)"),
    # The All names page's actions, queued for the bot and read back by the DM who made them.
    (
        "dmbot_web_name_action_add",
        "dmbot_web_name_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)",
    ),
    ("dmbot_web_name_action_read", "dmbot_web_name_action_read(BIGINT, TEXT, TEXT)"),
    # How a listed name is said, for the DM's row editor (#2317).
    ("dmbot_web_all_names_said", "dmbot_web_all_names_said(BIGINT, TEXT, TEXT[])"),
    # The Names page's search by meaning (#2733): the names nearest in meaning, DMs only.
    ("dmbot_web_names_near", "dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT)"),
    # The Settings page's choices (#2463), queued for the bot the same way.
    ("dmbot_web_setting_add", "dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)"),
    ("dmbot_web_setting_read", "dmbot_web_setting_read(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_optional_rules", "dmbot_web_optional_rules(BIGINT, TEXT)"),
    ("dmbot_web_optional_rule_add", "dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN)"),
    # The All names page's list (#2294): DMs, and managers without what only a DM sees.
    (
        "dmbot_web_all_names",
        "dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)",
    ),
    # The relationship web's data (#2323).
    (
        "dmbot_web_relationships",
        "dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)",
    ),
    # Every name the caller may see and which pairs are connected, for the Story graph's count line,
    # its "Not connected yet" tray and its other groups (#2632).
    ("dmbot_web_graph_overview", "dmbot_web_graph_overview(BIGINT, TEXT, INT, INT)"),
    # What waits for the DM: claims, a backstory's connections and "Which Mara?" questions (#2652).
    ("dmbot_web_waiting", "dmbot_web_waiting(BIGINT, TEXT)"),
    # What people believe: the readings that wait for the DM (#2412).
    ("dmbot_web_readings", "dmbot_web_readings(BIGINT, TEXT)"),
    # A move code asked for on the website (#1246).
    ("dmbot_web_move_code", "dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_cancel_move_code", "dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_forget_move_asks", "dmbot_web_forget_move_asks(BIGINT)"),
    ("dmbot_web_restore_target", "dmbot_web_restore_target(BIGINT, TEXT)"),
    ("dmbot_web_restore_add", "dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA)"),
    ("dmbot_web_restore_part", "dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA)"),
    ("dmbot_web_restore_go", "dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN)"),
    ("dmbot_web_restore_read", "dmbot_web_restore_read(BIGINT, TEXT)"),
    ("dmbot_web_restore_pending", "dmbot_web_restore_pending(BIGINT)"),
    ("dmbot_web_restore_cancel", "dmbot_web_restore_cancel(BIGINT, TEXT)"),
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
    # By email (#2532): the link's hash, the mark that says so, and delivered_at, set at once
    # because the bot has no Discord message to send for it (the policy allows that only here).
    "via_email",
    "token_hash",
    "delivered_at",
)
WEB_ROLE_GRANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SELECT", ("schema_migrations",)),
    # The person's own consent and character sheets, for the data export (#1150); the
    # restrictive web_own_rows policies hold it to their own rows in their servers. The right
    # to stop (DELETE) on consent is the website's Stop recording me (#1151): the same
    # policies hold it to the person's own row; it can never add or change a yes.
    ("SELECT", ("character_sheets",)),
    # A DM's recaps (#1382): read, edit the text, Post, Try again and Don't post. Never the
    # speakers, the call count, the original draft or the grade, which only the bot writes.
    ("SELECT", ("recap_jobs",)),
    (
        "UPDATE (summary, housekeeping, status, tries, state)",
        ("recap_jobs",),
    ),
    ("SELECT, DELETE", ("consent",)),
    # Accepting a hand-over (#614) makes the person the owner and one of the DMs; the
    # store locks the campaign row first (FOR UPDATE needs an UPDATE right), and answering
    # changes only an offer's status.
    ("SELECT, UPDATE (owner_user_id)", ("campaigns",)),
    ("SELECT, INSERT", ("campaign_dms",)),
    ("SELECT, UPDATE (status, decided_at)", ("campaign_handover_offers",)),
    (f"INSERT ({', '.join(OFFER_INSERT_COLUMNS)})", ("campaign_handover_offers",)),  # #2532
    ("SELECT, INSERT, UPDATE", ("installs", "entitlements")),
    ("SELECT, INSERT", ("payment_events", "try_it_used")),
    # The unsubscribe link (#1229) may be written, never read: the list stays the bot's. The
    # website sends its own account emails through dmbot_claim_email (WEB_ROLE_FUNCTIONS), and
    # has no grant on sent_emails.
    ("INSERT", ("email_unsubscribed",)),
    ("INSERT, DELETE", ("feedback",)),
    # Support tickets (#1340, #1373): added by the signed-in person; read back by them only as a
    # list of their own (`read_own`: when, subject, status), never the message, which has no
    # column right at all. The admin page reads the message through dmbot_support_message (a
    # door), and closes with the status UPDATE; the sweep and account deletion delete.
    ("INSERT, DELETE", ("support_tickets",)),
    (
        "SELECT (id, created_at, user_id, plan, subject, source, status, ai_next_at, ai_failed_at,"
        " escalated_reason, handed_over_reason, handed_over_at)",
        ("support_tickets",),
    ),
    (
        "UPDATE (status, ai_next_at, ai_failed_at, escalated_reason, handed_over_reason,"
        " handed_over_at)",
        ("support_tickets",),
    ),
    # The thread (#1431): replies are added and listed (not their words) only while the admin
    # door is open; the words come back through dmbot_support_thread.
    ("INSERT", ("support_replies",)),
    ("SELECT (id, ticket_id, who, created_at)", ("support_replies",)),
    # The weekly support email's once-only mark, through the admin door only (#1514).
    ("SELECT, INSERT, UPDATE", ("support_week_mail",)),
    # The IDs of the emails sent for a ticket, to thread the next one (#2192): admin door only.
    ("SELECT, INSERT", ("support_mail_ids",)),
    ("SELECT, INSERT, UPDATE, DELETE", ("web_users", "web_sessions")),
    # The improvement opt-out (#1609): the person's own row, through the website's switch.
    ("SELECT, INSERT, DELETE", ("improvement_opt_outs",)),
    # Emailed sign-in links (#1231): made and used by the website, never read by anyone else.
    # (Only `used_at` can be changed: a holder of a link can spend it, never revive or move it.)
    ("SELECT, INSERT, DELETE", ("email_sign_in_links",)),
    ("UPDATE (used_at)", ("email_sign_in_links",)),
    # A waiting change of the account's email (#2349): made, replaced, read, confirmed (deleted).
    ("SELECT, INSERT, UPDATE, DELETE", ("email_changes",)),
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

EXTRACTOR_PROGRESS = """
    -- How far the story extractor got through a stored session (#1367), kept on the session's
    -- own row (so it is that campaign's, behind the same row-level security, and goes with the
    -- session). `extracted_through_ms` is when the last line it read began (the lines' own
    -- clock, not a position, so removing a person's lines cannot move it): a restart reads only
    -- lines that began after it and never sends the same lines twice. `extractor_calls` is how
    -- many AI calls the session has used, and `extracted_at` when it last ran, for the
    -- campaign's daily ceiling. All zero for a session never read.
    ALTER TABLE transcript_sessions ADD COLUMN extracted_through_ms BIGINT NOT NULL DEFAULT 0
        CHECK (extracted_through_ms >= 0);
    ALTER TABLE transcript_sessions ADD COLUMN extractor_calls INT NOT NULL DEFAULT 0
        CHECK (extractor_calls >= 0);
    ALTER TABLE transcript_sessions ADD COLUMN extracted_at BIGINT;
"""

RECAP_JOBS = (
    _setting("dmbot_recap_mail_lookup", "dmbot.recap_mail_lookup", "TEXT")
    + _setting("dmbot_recap_mail", "dmbot.recap_mail", "TEXT")
    + f"""
    -- The cinematic recap of a session (#1382; docs/PLAN.md, "Cinematic recaps"): a job that runs
    -- after the session, away from the table, and emails the DM when it is ready. The DM reads,
    -- edits and posts it on the website; nothing is ever posted without them. Per campaign,
    -- never shared between campaigns or servers; in its backups; deleted with the campaign. One
    -- row for each session (by when it started); the latest earlier posted one is the bridge.
    --   state      the last stage that finished: queued, beats, checked, prose, housekeeping,
    --              ready, or failed. Each stage can be run again, so a restart picks up where it
    --              stopped (as the extractor does).
    --   attempt    failed checks of the stage now running (0 or 1: the second is the last)
    --   errors     calls that failed (the AI service, the network) in the stage now running
    --   next_at    not before this time (backoff after an error)
    --   leased_to  a worker has it until this time, so two never work on one job
    --   calls      AI calls for the recap now being written, against the per-recap cap
    --   window_count  how many parts the transcript is read in (a long session is read in
    --              overlapping windows), and window_done how many of them are done
    --   spent      AI calls ever made for this session (a Try again does not reset it), for
    --              the campaign's cap for the day
    --   last_error a short fixed reason (a type or a plain phrase), never the recap
    --   beats      the cited beat sheet: what happened, who, and the lines each rests on
    --   summary    the narration with its 《emotion》 and ((performance)) cues
    --   original   the draft as written, so the DM's edits can be counted against it
    --   music      one prompt for each emotion section (slice 2 turns them into audio)
    --   status     draft (the DM has not decided), posting (the DM pressed Post; the bot sends
    --              it), posted, or dropped
    --   tries      how many times the DM asked for another (at most 2)
    --   speakers   whose lines the recap was built from: asked about again at every stage
    --   grade      the grader's numbers (counts only, for quality over time)
    CREATE TABLE recap_jobs (
        {_memory_scope()}
        session_at   BIGINT NOT NULL CHECK (session_at > 0),
        dm_user_id   BIGINT NOT NULL CHECK (dm_user_id > 0),
        state        TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
                         ('queued', 'beats', 'checked', 'prose', 'housekeeping', 'audio',
                          'ready', 'failed')),
        attempt      INTEGER NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 2),
        errors       INTEGER NOT NULL DEFAULT 0 CHECK (errors BETWEEN 0 AND 3),
        restarts     INTEGER NOT NULL DEFAULT 0 CHECK (restarts BETWEEN 0 AND 3),
        next_at      BIGINT NOT NULL DEFAULT 0,
        leased_to    BIGINT NOT NULL DEFAULT 0,
        calls        INTEGER NOT NULL DEFAULT 0 CHECK (calls >= 0),
        spent        INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0),
        window_count INTEGER NOT NULL DEFAULT 0 CHECK (window_count BETWEEN 0 AND 12),
        window_done  INTEGER NOT NULL DEFAULT 0 CHECK (window_done BETWEEN 0 AND 12),
        last_error   TEXT NOT NULL DEFAULT '' CHECK (char_length(last_error) <= 200),
        problems     TEXT[] NOT NULL DEFAULT '{{}}',
        beats        JSONB NOT NULL DEFAULT '[]',
        title        TEXT NOT NULL DEFAULT '' CHECK (char_length(title) <= 120),
        summary      TEXT NOT NULL DEFAULT '' CHECK (char_length(summary) <= 6000),
        original     TEXT NOT NULL DEFAULT '' CHECK (char_length(original) <= 6000),
        housekeeping TEXT[] NOT NULL DEFAULT '{{}}',
        music        TEXT[] NOT NULL DEFAULT '{{}}',
        game_day     INTEGER CHECK (game_day IS NULL OR game_day > 0),
        status       TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'posting', 'posted', 'dropped')),
        tries        INTEGER NOT NULL DEFAULT 0 CHECK (tries BETWEEN 0 AND 2),
        speakers     BIGINT[] NOT NULL DEFAULT '{{}}',
        grade        JSONB NOT NULL DEFAULT '{{}}',
        emailed_at   BIGINT,
        created_at   BIGINT NOT NULL,
        updated_at   BIGINT NOT NULL DEFAULT 0,
        decided_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_at)
    );
    CREATE INDEX recap_jobs_due ON recap_jobs (guild_id, state) WHERE state NOT IN
        ('ready', 'failed');
    """
    + _isolate("recap_jobs")
    + """
    -- The account address a recap's email goes to (the same one the Support replies use: the
    -- payment company's, else the one Discord gave). web_users is forced to a person's own row,
    -- so reading the DM's needs a door like dmbot_user_by_email's: a function with its owner's
    -- rights that answers only while Database.recap_mail() has opened it, and only for a person
    -- who is a DM of a campaign with a recap job in the server it was opened for. A read-only
    -- policy TO the owner role, open only while the function has set its own setting.
    CREATE FUNCTION dmbot_recap_address(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.recap_mail_lookup', true), '');
    BEGIN
        IF dmbot_recap_mail() IS DISTINCT FROM 'mail' THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs WHERE dm_user_id = who) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_mail_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.recap_mail_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_mail_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_recap_mail_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_address(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_address(BIGINT) FROM PUBLIC;
    """
)

RECAP_AUDIO_ADDON = """
    -- The recap audio add-on (#1389; owner decision on #1381): a second, monthly subscription
    -- that adds spoken and musical recaps to a paid plan that does not include them (Guild and
    -- Pro do). Like extra hours it is a row on the account, written only by the payment writer
    -- (the guard on `entitlements` already allows nothing else).
    --   recap_audio_until            the end of the period paid for; the add-on works until then
    --   recap_audio_subscription_id  the company's subscription, so it can be cancelled with the
    --                                plan or the account
    --   recap_audio_event_at         the newest add-on event applied, so a late one is ignored
    ALTER TABLE entitlements ADD COLUMN recap_audio_until BIGINT;
    ALTER TABLE entitlements ADD COLUMN recap_audio_subscription_id TEXT;
    ALTER TABLE entitlements ADD COLUMN recap_audio_event_at BIGINT NOT NULL DEFAULT 0;
    """

RECAP_AUDIO_FOLLOW_UPS = """
    -- Follow-ups to the recap audio add-on (#1399).
    -- 1. A move retargets the add-on's subscription too. The move records the add-on's
    --    subscription id next to the plan's (a trigger fills it in from the account's row, which
    --    has already followed the move when the marker is added), and the job that updates the
    --    payment company is told about it.
    ALTER TABLE account_moves ADD COLUMN addon_subscription_id TEXT;
    CREATE FUNCTION dmbot_move_addon() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        SELECT e.recap_audio_subscription_id INTO NEW.addon_subscription_id
            FROM entitlements e WHERE e.user_id = NEW.new_user_id;
        IF NEW.addon_subscription_id IS NOT NULL THEN
            NEW.stripe_done_at := NULL;  -- there is a record at the company still to point
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER account_moves_addon BEFORE INSERT ON account_moves
        FOR EACH ROW EXECUTE FUNCTION dmbot_move_addon();
    DROP FUNCTION dmbot_moves_to_sync();
    CREATE FUNCTION dmbot_moves_to_sync()
        RETURNS TABLE (old_user_id BIGINT, new_user_id BIGINT, customer_id TEXT,
                       subscription_id TEXT, addon_subscription_id TEXT)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- Never tried first, then the longest ago; one that failed waits a minute, two,
        -- four... up to an hour, so a few stuck moves can't crowd out a new one.
        SELECT m.old_user_id, m.new_user_id, m.customer_id, m.subscription_id,
               m.addon_subscription_id
            FROM account_moves m
            WHERE m.stripe_done_at IS NULL
              AND (m.stripe_last_try_at IS NULL
                   OR m.stripe_last_try_at + LEAST(60 * (2 ^ LEAST(m.stripe_attempts, 6))::BIGINT,
                                                   3600) <= extract(epoch FROM now())::BIGINT)
            ORDER BY m.stripe_last_try_at NULLS FIRST, m.moved_at
            LIMIT 20;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_moves_to_sync() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_addon() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_moves_to_sync() FROM PUBLIC;
    -- 2. An add-on subscription that must not exist and could not be cancelled is kept here, so
    --    the hourly sweep tries again. It holds only the company's subscription id (no person's
    --    data); the website reaches it through four doors, never the table.
    CREATE TABLE recap_audio_strays (
        subscription_id TEXT PRIMARY KEY
                        CHECK (subscription_id ~ '^sub_[A-Za-z0-9_]{1,90}$'),  -- a company id only
        found_at        BIGINT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_try_at     BIGINT
    );
    CREATE FUNCTION dmbot_note_stray_addon(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        INSERT INTO recap_audio_strays (subscription_id, found_at, attempts, last_try_at)
            VALUES (sub, extract(epoch FROM now())::BIGINT, 1, extract(epoch FROM now())::BIGINT)
            ON CONFLICT (subscription_id) DO UPDATE SET attempts = recap_audio_strays.attempts + 1,
                last_try_at = EXCLUDED.last_try_at;
    $fn$;
    CREATE FUNCTION dmbot_strays_to_cancel() RETURNS TABLE (subscription_id TEXT, attempts INTEGER)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- At most once an hour each, oldest first, a few at a time.
        SELECT s.subscription_id, s.attempts FROM recap_audio_strays s
            WHERE s.last_try_at IS NULL
               OR s.last_try_at + 3600 <= extract(epoch FROM now())::BIGINT
            ORDER BY s.found_at LIMIT 20;
    $fn$;
    CREATE FUNCTION dmbot_stray_addon_tried(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE recap_audio_strays SET attempts = attempts + 1,
            last_try_at = extract(epoch FROM now())::BIGINT WHERE subscription_id = sub;
    $fn$;
    CREATE FUNCTION dmbot_stray_addon_done(sub TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        DELETE FROM recap_audio_strays WHERE subscription_id = sub;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_note_stray_addon(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_strays_to_cancel() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_stray_addon_tried(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_stray_addon_done(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_note_stray_addon(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_strays_to_cancel() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_stray_addon_tried(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_stray_addon_done(TEXT) FROM PUBLIC;
    """

RECAP_AUDIO_JOBS = (
    """
    -- The recap's two audio tracks (#1383), made after the text is ready; the text never waits
    -- for them. Per recap, kept on its row; the files themselves are on disk, not here.
    --   audio         off (not wanted, or no key), queued, ready or failed
    --   audio_tries   attempts so far (one retry later, then failed)
    --   audio_next_at not before this time (the wait before the retry)
    --   audio_credits the provider credits this making has cost, across its tries (a count);
    --                 a remake starts again at 0 (#2183)
    --   audio_error   a short fixed reason, never a name or any text
    ALTER TABLE recap_jobs ADD COLUMN audio TEXT NOT NULL DEFAULT 'off'
        CHECK (audio IN ('off', 'queued', 'ready', 'failed'));
    ALTER TABLE recap_jobs ADD COLUMN audio_tries INTEGER NOT NULL DEFAULT 0
        CHECK (audio_tries BETWEEN 0 AND 3);
    ALTER TABLE recap_jobs ADD COLUMN audio_next_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN audio_text_hash TEXT NOT NULL DEFAULT ''
        CHECK (char_length(audio_text_hash) <= 64);
    ALTER TABLE recap_jobs ADD COLUMN audio_credits INTEGER NOT NULL DEFAULT 0
        CHECK (audio_credits >= 0);
    ALTER TABLE recap_jobs ADD COLUMN audio_error TEXT NOT NULL DEFAULT ''
        CHECK (char_length(audio_error) <= 200);
    --   audio_text_hash  a fingerprint of the title and text the audio was made from: after the
    --                 DM edits the text the audio no longer matches and is not attached
    -- A campaign's pronunciation dictionary at the voice company, made once and kept here with
    -- a hash of its rules, so it is made again only when a name's spelling changes. Only
    -- non-secret names are ever in it. It belongs to this campaign alone and goes with it.
    CREATE TABLE recap_dictionaries (
        """
    + "{scope}"
    + """
        dictionary_id TEXT NOT NULL CHECK (char_length(dictionary_id) BETWEEN 1 AND 100),
        version_id    TEXT NOT NULL CHECK (char_length(version_id) BETWEEN 1 AND 100),
        rules_hash    TEXT NOT NULL CHECK (char_length(rules_hash) = 64),
        updated_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """
    + _isolate("recap_dictionaries")
)
RECAP_AUDIO_JOBS = RECAP_AUDIO_JOBS.replace("{scope}", _memory_scope())

RECAP_AUDIO_AGAIN = """
    -- "Make the audio again" on the website (#1418). The website's role may only READ the audio
    -- columns of a recap (its grant names the columns it may change), so asking for new audio
    -- goes through this door: only a DM of the campaign, only a recap that is ready and still
    -- the DM's to post, and at most once every 10 minutes for a recap (audio_asked_at, the
    -- database's own clock, so a caller cannot lie about the time). It does what the bot does
    -- to queue audio and nothing else: audio queued, tries and wait back to none.
    ALTER TABLE recap_jobs ADD COLUMN audio_asked_at BIGINT NOT NULL DEFAULT 0;
    CREATE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        -- Only from where the page offers the button: made, or failed. Never while the bot is
        -- making it (its tries and wait would be reset under it) or when it was never wanted.
        IF NOT FOUND OR job.state <> 'ready' OR job.status NOT IN ('draft', 'posting')
           OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

WEB_NAMES = """
    -- The campaign's names on the website, with the narrator's "said as" spelling (#1451, on
    -- #1419). The website's role has NO grant on the memory tables (secret names live there), so
    -- both the read and the write go through these two doors. Each answers only a DM of the
    -- campaign, in a server on the session's list, with that server set for the transaction (the
    -- caller does the same for every server table), and never a secret name or a person.
    --
    -- dmbot_web_names: the confirmed, public names of things and places (not people at the table,
    -- not a sound-alike "misheard" note), at most 200, as JSON; NULL when the caller is no DM of
    -- the campaign, so the page says "not found" exactly as for a missing campaign.
    CREATE FUNCTION dmbot_web_names(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', n.id, 'text', n.text, 'kind', n.kind, 'saidAs', n.said_as))
            FROM (
                SELECT a.id, a.text, a.kind, a.said_as
                FROM memory_aliases a
                JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND a.status = 'confirmed'
                  AND e.status = 'confirmed' AND e.type <> 'person'
                  AND a.kind <> 'misheard' AND a.used_by IS NULL
                ORDER BY lower(e.name), lower(a.text), a.id
                LIMIT 200
            ) n), '[]'::jsonb);
    END
    $fn$;

    -- dmbot_web_set_said_as: set or clear (NULL or empty) one name's spelling. The same rules as
    -- MemoryStore.set_said_as: only a DM, up to 80 characters, no control characters, never on a
    -- secret name. It writes through the change log exactly as the bot does (one numbered batch,
    -- the row's before and after, the campaign's memory version, the change notice the bot's
    -- name lists follow), so Undo on the Discord name card still takes it back. Answers
    -- 'ok', 'not_allowed' (not this campaign's DM), 'not_found' (no such name, or a secret one) or
    -- 'too_long'. The 80-character limit and the control-character check are the database's own
    -- floor; the API (web/names.py) applies the bot's fuller printable-characters rule first.
    -- Any listed name may be given a spelling, not only an entry's main name: the narrator reads
    -- the spelling from every name (#1419), and the page lists exactly what the read door lists.
    CREATE FUNCTION dmbot_web_set_said_as(g BIGINT, c TEXT, alias_id TEXT, spelling TEXT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        clean TEXT := NULLIF(
            btrim(regexp_replace(COALESCE(spelling, ''), '[[:space:]]+', ' ', 'g')), '');
        alias_row memory_aliases%ROWTYPE;
        v BIGINT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        IF clean IS NOT NULL AND (char_length(clean) > 80 OR clean ~ '[[:cntrl:]]') THEN
            RETURN 'too_long';
        END IF;
        -- Changes to one campaign's memory happen one at a time (the bot takes the same lock).
        SELECT memory_version INTO v FROM campaigns
            WHERE guild_id = g AND id = c FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        SELECT a.* INTO alias_row FROM memory_aliases a
            JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = alias_id
              AND NOT a.secret AND a.status = 'confirmed' AND e.status = 'confirmed'
              AND e.type <> 'person' AND a.kind <> 'misheard' AND a.used_by IS NULL;
        -- A secret name is "not found", the same answer as an id that does not exist, so the
        -- caller cannot learn that a secret name with that id exists.
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        IF alias_row.said_as IS NOT DISTINCT FROM clean THEN
            RETURN 'ok';  -- nothing to change, nothing to log (as the bot does)
        END IF;
        UPDATE memory_aliases SET said_as = clean
            WHERE guild_id = g AND campaign_id = c AND id = alias_id;
        v := v + 1;
        INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name, row_id, op,
                                    before, after, source, undoes, made_at)
            SELECT g, c, v, v, 'memory_aliases', alias_id, 'update',
                   to_jsonb(alias_row) - 'guild_id' - 'campaign_id',
                   to_jsonb(a) - 'guild_id' - 'campaign_id', 'dm', NULL, now_s
            FROM memory_aliases a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = alias_id;
        UPDATE campaigns SET memory_version = v WHERE guild_id = g AND id = c;
        PERFORM pg_notify('dmbot_memory', c || ':' || v || ':1');
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_names(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_names(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""

# WEB_NAMES from its first function on (its comments describe the first migration), reused by
# OLD_SPELLING to replace the two functions.
_WEB_NAMES_FUNCTIONS = "    CREATE OR REPLACE FUNCTION dmbot_web_names" + WEB_NAMES.split(
    "    CREATE FUNCTION dmbot_web_names", 1
)[1].replace(
    "    CREATE FUNCTION dmbot_web_set_said_as",
    "    CREATE OR REPLACE FUNCTION dmbot_web_set_said_as",
)

MEMORY_SAID_AS = """
    -- A spelling for the recap's narrator (#1419): "Kah-zeth" for Ka'zeth. Set by the DM on a name,
    -- through the same change path as every other edit (so it can be undone), and only on a name
    -- that is not secret: the voice company is sent these, and a secret name never leaves DMbot.
    -- The check makes the database refuse the pair even if code forgot.
    ALTER TABLE memory_aliases ADD COLUMN said_as TEXT
        CHECK (said_as IS NULL OR char_length(said_as) BETWEEN 1 AND 80);
    ALTER TABLE memory_aliases ADD CONSTRAINT memory_aliases_secret_not_said
        CHECK (NOT secret OR said_as IS NULL);
    """

RECAP_SWITCHES = """
    -- The DM's recap choices on the campaign (#1421): write a recap after each session, make
    -- the narration and music, email them when it is ready. All on by default; they can only
    -- turn things off, never on past what the plan allows. They travel in a backup with the
    -- campaign's other settings.
    ALTER TABLE campaigns ADD COLUMN recaps_text BOOLEAN NOT NULL DEFAULT TRUE;
    ALTER TABLE campaigns ADD COLUMN recaps_audio BOOLEAN NOT NULL DEFAULT TRUE;
    ALTER TABLE campaigns ADD COLUMN recaps_email BOOLEAN NOT NULL DEFAULT TRUE;
    """

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
SUPPORT_EMAIL = (
    _setting("dmbot_support_email_lookup", "dmbot.support_email_lookup", "TEXT")
    + """
    -- The email a support ticket's answer goes to (#1340; Supervisor, 2026-10-15: the team replies
    -- by email to the address on the person's account). The admin page shows it beside
    -- each ticket; it is never copied into the ticket, so it stays the account's own and goes with
    -- the account. Same address the site uses for the account: the payment company's, else the
    -- one Discord gave (web.accounts.account_email).
    -- web_users is forced to a person's own row, so reading another person's needs a door like
    -- dmbot_user_by_email's: a function with its owner's rights that answers only while the admin
    -- door (Database.support_admin) is open, and a read-only policy TO the owner role, open only
    -- while the function has set its own setting. Never the website's role, which could otherwise
    -- set that setting itself and read every account's email.
    CREATE FUNCTION dmbot_support_email(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.support_email_lookup', true), '');
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN NULL;
        END IF;
        -- Only for an account that has sent a ticket: the door is one the website's role can open
        -- itself, so it must not be a way to read any account's address. (The ticket check runs
        -- under the admin door the caller already holds; support_tickets is forced to it.)
        IF NOT EXISTS (SELECT 1 FROM support_tickets WHERE user_id = who) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.support_email_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.support_email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY support_email_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_support_email_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_email(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_email(BIGINT) FROM PUBLIC;
    """
)

SUPPORT_OWN = """
    -- A person sees their own questions (#1373): when they sent it, the subject and where it
    -- stands. Never the message, and never anyone else's.
    --   * `read_own` lets a person's session read only rows with their own user_id.
    --   * The website's role has a column right on every column of support_tickets EXCEPT
    --     `message` (WEB_ROLE_GRANTS), so even their own words can't be read back through that
    --     role. The admin page reads the words through dmbot_support_message below, a door that
    --     answers only while the admin door is open (Database.support_admin), exactly like
    --     dmbot_support_email: a Python boundary (tests/test_support.py) whose limits are the
    --     same as before this migration, when the admin page could read the column itself.
    CREATE POLICY read_own ON support_tickets FOR SELECT
        USING (user_id = dmbot_current_user());
    -- The list is one person's tickets, newest first.
    CREATE INDEX support_tickets_by_person ON support_tickets (user_id, created_at DESC);
    CREATE FUNCTION dmbot_support_message(ticket BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        words TEXT;
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN NULL;
        END IF;
        -- support_tickets is forced to its policies: with the door open, support_admin_read
        -- lets this read the row (the function's owner has the column right).
        SELECT message INTO words FROM support_tickets WHERE id = ticket;
        RETURN words;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_message(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_message(BIGINT) FROM PUBLIC;
    """

MEMORY_FROM_SET = """
    -- A relationship definition borrowed from a followed shared rule set (#1456; docs/PLAN.md,
    -- "Shared rulesets"): the set's id, null for the campaign's own and for the official ones.
    -- Read-only while it is the set's. A column with no default and no backfill: every existing
    -- row is the campaign's own, which null already says, so no row changes and no row-level
    -- security needs opening.
    ALTER TABLE memory_predicates ADD COLUMN from_set TEXT
        CHECK (from_set IS NULL OR from_set ~ '^[0-9a-f]{32}$');
    """

SUPPORT_THREAD = """
    -- The support flow (#1431): a ticket becomes a thread. The person's later messages, the AI
    -- helper's answers and the team's replies are rows of `support_replies`; the first message
    -- stays in support_tickets.message. Like that column, a reply's words can be read only through
    -- a door that answers while the admin door (Database.support_admin) is open
    -- (dmbot_support_thread), and the website's role has no column right on them.
    --   who  customer (an emailed reply), ai (DMbot's helper), person (the team)
    -- A reply goes with its ticket (the cascade is not blocked by row-level security), so the
    -- 12-month sweep and account deletion need nothing more.
    ALTER TABLE support_tickets DROP CONSTRAINT support_tickets_status_check;
    ALTER TABLE support_tickets ADD CONSTRAINT support_tickets_status_check
        CHECK (status IN ('new', 'answered', 'closed', 'escalated'));
    -- When the helper may try this ticket again after a failed call, and when its calls began to
    -- fail (a ticket whose calls fail for a day goes to a person).
    ALTER TABLE support_tickets ADD COLUMN ai_next_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE support_tickets ADD COLUMN ai_failed_at BIGINT;
    CREATE TABLE support_replies (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ticket_id  BIGINT NOT NULL REFERENCES support_tickets (id) ON DELETE CASCADE,
        who        TEXT NOT NULL CHECK (who IN ('customer', 'ai', 'person')),
        text       TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
        created_at BIGINT NOT NULL
    );
    CREATE INDEX support_replies_by_ticket ON support_replies (ticket_id, id);
    ALTER TABLE support_replies ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_replies FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_read ON support_replies FOR SELECT
        USING (dmbot_support_admin() = 'admin');
    CREATE POLICY support_admin_add ON support_replies FOR INSERT
        WITH CHECK (dmbot_support_admin() = 'admin');
    CREATE FUNCTION dmbot_support_thread(ticket BIGINT)
        RETURNS TABLE (id BIGINT, who TEXT, text TEXT, created_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF dmbot_support_admin() IS DISTINCT FROM 'admin' THEN
            RETURN;
        END IF;
        RETURN QUERY SELECT r.id, r.who, r.text, r.created_at FROM support_replies r
            WHERE r.ticket_id = ticket ORDER BY r.id;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_thread(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_thread(BIGINT) FROM PUBLIC;
    """

SUPPORT_MINE_ANSWER = """
    -- A person's own latest answer, for the account page's "Your questions" (#1432): who answered
    -- (the helper, or the team) and the answer's words, so the page can show its first line.
    -- Only an answer (never the person's own emailed replies) and only on a ticket of the
    -- signed-in person (`dmbot_current_user()`, set by Database.user), never anyone else's. The
    -- website's role has no column right on a reply's words, so this door is the only way they
    -- come back, and the policy below lets the door (not the role's own queries, which have no
    -- right on the words) see a person's own answers. (The policy also lets a person's own session
    -- list the ids, kinds and times of their own answers, which the role may already read in
    -- those four columns: the answers themselves come to them by email, so nothing is exposed.)
    CREATE POLICY read_own_answers ON support_replies FOR SELECT
        USING (who IN ('ai', 'person') AND EXISTS (
            SELECT 1 FROM support_tickets t
            WHERE t.id = support_replies.ticket_id AND t.user_id = dmbot_current_user()));
    CREATE FUNCTION dmbot_support_latest_answer(ticket BIGINT)
        RETURNS TABLE (who TEXT, text TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        RETURN QUERY SELECT r.who, r.text FROM support_replies r
            WHERE r.ticket_id = ticket AND r.who IN ('ai', 'person')
              AND EXISTS (SELECT 1 FROM support_tickets t
                          WHERE t.id = ticket AND t.user_id = dmbot_current_user())
            ORDER BY r.id DESC LIMIT 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_latest_answer(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_latest_answer(BIGINT) FROM PUBLIC;
    """

SUPPORT_MINE_THREAD = """
    -- A person reads the whole conversation of one of their own tickets (#2193; docs/PLAN.md,
    -- "Support flow"): their question, the helper's answers, their emailed replies and the team's
    -- replies, oldest first. Like the latest-answer door, it asks only for a ticket whose
    -- user_id is the signed-in person (`dmbot_current_user()`, set by Database.user), so another
    -- account's ticket and a missing one look the same (no rows). The forced `read_own` policy on
    -- support_tickets says the same thing a second time: two layers, either of which holds.
    -- The website's role still has no column right on any of the words: this door is the only
    -- way they come back.
    -- A ticket is named by its sent time because the list shows no ticket numbers (they would
    -- show how many other people write); a person's tickets never share a sent time (the insert
    -- stamps a second ticket one second later), and if one ever did the newer would be shown.
    -- The policy lets the door (not the role's own queries, which have no right on the words) see
    -- a person's own emailed replies too; the ids, kinds and times of those are the only new thing
    -- a person's own session could list, and they are their own messages.
    CREATE POLICY read_own_replies ON support_replies FOR SELECT
        USING (who = 'customer' AND EXISTS (
            SELECT 1 FROM support_tickets t
            WHERE t.id = support_replies.ticket_id AND t.user_id = dmbot_current_user()));
    CREATE FUNCTION dmbot_support_mine_thread(sent BIGINT)
        RETURNS TABLE (who TEXT, text TEXT, created_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        mine support_tickets%ROWTYPE;
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        SELECT * INTO mine FROM support_tickets t
            WHERE t.user_id = dmbot_current_user() AND t.created_at = sent
            ORDER BY t.id DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        RETURN QUERY
            SELECT o.who, o.text, o.created_at FROM (
                SELECT 0::BIGINT AS ord, 'customer'::TEXT AS who, mine.message AS text,
                       mine.created_at AS created_at
                UNION ALL
                SELECT r.id, r.who, r.text, r.created_at FROM support_replies r
                WHERE r.ticket_id = mine.id
            ) o ORDER BY o.ord;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_mine_thread(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_mine_thread(BIGINT) FROM PUBLIC;
    """

SUPPORT_TICKET_TIMES = """
    -- A person's tickets never share a sent time (#2214): the account page opens a ticket by that
    -- time (#2193). `support.add` stamps the next free second; this index is what holds two sends
    -- in the same instant apart, whichever commits second retries.
    -- Tickets already sharing a time are moved apart first: each pass moves every ticket that
    -- shares its second with an earlier id up by one, until none do. A ticket that was alone can
    -- be pushed too when the ones below it run into it, so a cluster of k ends up at most k-1
    -- seconds late; the order by id is kept. (The table has forced row-level security, so the
    -- migration opens it for itself and closes it again, as the other backfills do.) The older
    -- index (user_id, created_at DESC) is left as it is: dropping an index is not this change.
    CREATE POLICY migrate_backfill ON support_tickets FOR ALL USING (true) WITH CHECK (true);
    DO $do$
    BEGIN
        LOOP
            UPDATE support_tickets t SET created_at = t.created_at + 1
            FROM (SELECT id, row_number() OVER (PARTITION BY user_id, created_at ORDER BY id) AS n
                  FROM support_tickets) d
            WHERE t.id = d.id AND d.n > 1;
            EXIT WHEN NOT FOUND;
        END LOOP;
    END
    $do$;
    DROP POLICY migrate_backfill ON support_tickets;
    CREATE UNIQUE INDEX support_tickets_one_time ON support_tickets (user_id, created_at);
    """

WEB_RECAP_CHOICES = """
    -- The DM's three recap choices on the website (#1468), the same as the DM screen's 📜 Recaps
    -- panel (#1421): recaps on or off, the narration and music, the email. The website's role has
    -- no right to write `campaigns`, so both the read and the write go through these doors. Each
    -- answers only a DM of the campaign (`campaign_dms` and `dmbot_current_user()`: never a server
    -- manager, as in the panel), in a server on the session's list, with that server set for the
    -- transaction. The choices can only turn things off in effect: the bot's own plan gate decides
    -- at the time of each recap (it fails closed), and the API refuses to turn the narration on for
    -- a plan that does not have it.
    CREATE FUNCTION dmbot_web_recap_choices(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        -- The owner decides the plan (the bot reads the owner's plan), so it comes back too: the
        -- API says whether the narration may be turned on only when the DM is that owner.
        RETURN jsonb_build_object(
            'text', camp.recaps_text, 'audio', camp.recaps_audio, 'email', camp.recaps_email,
            'owner', camp.owner_user_id::TEXT);
    END
    $fn$;

    -- Answers 'ok', 'not_allowed' (not this campaign's DM), 'not_found' or 'bad_choice'.
    CREATE FUNCTION dmbot_web_set_recap_choice(g BIGINT, c TEXT, which TEXT, turn_on BOOLEAN)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        IF which = 'text' THEN
            UPDATE campaigns SET recaps_text = turn_on WHERE guild_id = g AND id = c;
        ELSIF which = 'audio' THEN
            UPDATE campaigns SET recaps_audio = turn_on WHERE guild_id = g AND id = c;
        ELSIF which = 'email' THEN
            UPDATE campaigns SET recaps_email = turn_on WHERE guild_id = g AND id = c;
        ELSE
            RETURN 'bad_choice';
        END IF;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_choices(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_recap_choices(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SUPPORT_REASON = """
    -- Why a ticket was handed to a person (#1459): one short code set where the helper escalates
    -- it, shown on the admin page. Null for a ticket that was never escalated or has been
    -- answered since. No backfill: tickets escalated before this have no recorded reason, which
    -- null already says, so no row changes and no row-level security needs opening.
    ALTER TABLE support_tickets ADD COLUMN escalated_reason TEXT
        CHECK (escalated_reason IS NULL OR escalated_reason IN
               ('unhappy', 'asked', 'limit', 'no-email', 'helper-down'));
    """

SUPPORT_HANDED_OVER = """
    -- Every hand-over, kept for good (#1487). `escalated_reason` says why a ticket is waiting for
    -- a person now and is cleared when one answers; these two are set at the same moment the
    -- helper hands the ticket over and never cleared, so the week's count of hand-overs stays
    -- complete and the admin page can say how long one has waited. No backfill: tickets handed
    -- over before this have neither, which null already says, so no row changes and no
    -- row-level security needs opening.
    ALTER TABLE support_tickets
        ADD COLUMN handed_over_reason TEXT
            CHECK (handed_over_reason IS NULL OR handed_over_reason IN
                   ('unhappy', 'asked', 'limit', 'no-email', 'helper-down')),
        ADD COLUMN handed_over_at BIGINT;
    """

TRANSCRIPT_SUGGESTIONS = """
    -- A player's suggested fix to their own line of the cleaned transcript (#1491). The DM
    -- decides; only the line's `text` ever changes, never what was heard. `started_ms` and
    -- `user_id` name the line (like transcript_lines). One open suggestion for a line at a
    -- time; one that waits past the end of the next session is `dropped` (no message). A new
    -- table, so no existing row changes and no row-level security needs opening.
    CREATE TABLE transcript_suggestions (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        started_ms BIGINT NOT NULL,
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        suggested  TEXT NOT NULL CHECK (char_length(suggested) BETWEEN 1 AND 200),
        -- The line's cleaned words when it was suggested: Accept refuses a line that changed since.
        base_text  TEXT NOT NULL CHECK (char_length(base_text) <= 4000),
        state      TEXT NOT NULL DEFAULT 'open'
                       CHECK (state IN ('open', 'accepted', 'declined', 'dropped')),
        created_at BIGINT NOT NULL,
        decided_at BIGINT,
        decided_by BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX transcript_suggestions_one_open
        ON transcript_suggestions (session_id, user_id, started_ms) WHERE state = 'open';
    """ + _isolate("transcript_suggestions")

WAITING_NAME_QUESTIONS = """
    -- "Did they mean…?" questions nobody answered during a session (#1572; docs/PLAN.md,
    -- "Transcript Cleaner"): kept for the after-session check, so the DM can still say that
    -- "Marin" was Maren. The line is named like transcript_suggestions names one (`session_id`,
    -- `speaker_id`, `started_ms`): its words are read from the line when the DM answers, so
    -- nothing of the line is copied here. `heard` is the misheard word and `option_ids` the
    -- 2 or 3 names it might be (entry IDs, read again when asked, since a name can be removed
    -- or made secret meanwhile). One row for a word in a line; open ones expire after 7 days
    -- (state `expired`, no message). A new table, so no existing row changes and no row-level
    -- security needs opening.
    CREATE TABLE waiting_name_questions (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        speaker_id  BIGINT NOT NULL CHECK (speaker_id > 0),
        started_ms  BIGINT NOT NULL,
        heard       TEXT NOT NULL CHECK (char_length(heard) BETWEEN 1 AND 100),
        option_ids  TEXT[] NOT NULL CHECK (cardinality(option_ids) BETWEEN 2 AND 3),
        state       TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'answered', 'kept', 'expired')),
        created_at  BIGINT NOT NULL,
        decided_at  BIGINT,
        decided_by  BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX waiting_name_questions_one_per_word
        ON waiting_name_questions (session_id, speaker_id, started_ms, heard);
    CREATE INDEX waiting_name_questions_open
        ON waiting_name_questions (campaign_id, state, created_at);
    """ + _isolate("waiting_name_questions")

HOMEBREW_ENTRIES = f"""
    -- A campaign's own homebrew (#1520; docs/PLAN.md, "Homebrew (part 1)"): spells, monsters,
    -- items, feats, conditions and rules the DM made, in the DM's own words. Per campaign, never
    -- shared between campaigns or servers; deleted with the campaign; in its backups. Only the
    -- campaign's DMs change them (dmbot.rules.homebrew checks that in the same transaction).
    --   name_key          the name, case-folded, unique per campaign
    --   players_may_know  false by default: a homebrew monster is a spoiler, so an entry is
    --                     DM-screen content until the DM says otherwise
    --   version           counts changes, so an edit or removal made from an old view is told
    --                     from one made on the current entry
    -- A new table: no existing row changes, so no row-level security needs opening.
    CREATE TABLE homebrew_entries (
        id               BIGINT GENERATED ALWAYS AS IDENTITY,
        {_memory_scope()}
        kind             TEXT NOT NULL
                             CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition',
                                             'rule', 'other')),
        name             TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        -- (case-folding can make a letter up to three, so an 80-letter name fits in 240)
        name_key         TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 240),
        text             TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        summary          TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        players_may_know BOOLEAN NOT NULL DEFAULT false,
        created_by       BIGINT NOT NULL CHECK (created_by > 0),
        created_at       BIGINT NOT NULL,
        updated_at       BIGINT NOT NULL,
        version          INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        PRIMARY KEY (id),
        UNIQUE (guild_id, campaign_id, name_key)
    );
    """ + _isolate("homebrew_entries")

SUPPORT_WEEK_MAIL = """
    -- The week the team's weekly support email was last sent for (#1514): one row, so a restart
    -- never sends it twice. `week` is the day number (Python's date ordinal) of the Monday the
    -- send was for. Counts only; nothing in it is private. A new table, so no existing row changes
    -- and no row-level security needs opening; only the support admin door may read or write it.
    CREATE TABLE support_week_mail (
        id   SMALLINT PRIMARY KEY CHECK (id = 1),
        week BIGINT NOT NULL
    );
    ALTER TABLE support_week_mail ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_week_mail FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_only ON support_week_mail
        USING (dmbot_support_admin() = 'admin')
        WITH CHECK (dmbot_support_admin() = 'admin');
    """

SUPPORT_MAIL_IDS = """
    -- The Message-ID of every email sent for a support ticket (#2192), so the next one can say
    -- which it answers (In-Reply-To, References) and a mail program shows the whole chain as one
    -- conversation. Random IDs only: no words, no addresses. They go with the ticket (the
    -- cascade), and only the admin door reads or writes them, like the replies.
    CREATE TABLE support_mail_ids (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ticket_id  BIGINT NOT NULL REFERENCES support_tickets (id) ON DELETE CASCADE,
        message_id TEXT NOT NULL
            CHECK (message_id ~ '^<[^<>[:space:]@]{1,100}@[^<>[:space:]@]{1,100}>$'),
        created_at BIGINT NOT NULL
    );
    CREATE INDEX support_mail_ids_by_ticket ON support_mail_ids (ticket_id, id);
    ALTER TABLE support_mail_ids ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_mail_ids FORCE ROW LEVEL SECURITY;
    CREATE POLICY support_admin_only ON support_mail_ids
        USING (dmbot_support_admin() = 'admin')
        WITH CHECK (dmbot_support_admin() = 'admin');
    """

WEB_TRANSCRIPTS = """
    -- A campaign's session transcripts on the website (#1501). Transcripts are shared: anyone in
    -- the Discord server may read and download them (CLAUDE.md, decided 2026-10-05), so these
    -- doors answer any signed-in person whose session lists the server (`dmbot_web_guilds()`), not
    -- only a DM, and only with that server set for the transaction (the caller does that for
    -- every server table). They only read. The website's role has no grant on the transcript
    -- tables. What a reader is shown (cleaned or as heard, and what each leaves out) is decided in
    -- Python by the bot's own `transcript/export.py`, so there is one formatter: these doors hand
    -- over the stored rows as they are, exactly what `TranscriptStore.lines` reads for Discord.
    --
    -- dmbot_web_transcript_sessions: the campaign's sessions that have lines, newest first, with
    -- their number (Session 1, 2, ... in order, as the bot numbers them), and the campaign's
    -- owner (the plan that allows transcripts is the owner's, #437). NULL when the caller is not
    -- in the server or the campaign does not exist.
    CREATE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'owner', camp.owner_user_id::TEXT,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines)) ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                    FROM transcript_sessions t
                    WHERE t.guild_id = g AND t.campaign_id = c AND t.line_count > 0
                ) s), '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_lines: one session's lines in the order the speech started, every
    -- column `TranscriptStore.lines` reads. NULL when the caller is not in the server or the
    -- session is not this campaign's.
    CREATE FUNCTION dmbot_web_transcript_lines(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'startedMs', l.started_ms, 'userId', l.user_id::TEXT,
                       'heard', l.heard, 'text', COALESCE(l.text, l.heard),
                       'durationMs', l.duration_ms, 'topic', l.topic,
                       'entity', COALESCE(l.entity, ''), 'sidebar', COALESCE(l.sidebar, ''),
                       'ref', l.sidebar_ref, 'replyTo', l.sidebar_reply_to,
                       'via', l.sidebar_via, 'stt', l.sidebar_stt, 'model', l.sidebar_model,
                       'prompt', l.sidebar_prompt, 'sources', l.sidebar_sources,
                       'typed', l.typed) ORDER BY l.started_ms, l.id)
            FROM transcript_lines l
            WHERE l.guild_id = g AND l.session_id = sid), '[]'::jsonb);
    END
    $fn$;
    -- dmbot_web_transcript_campaigns: the server's campaigns that have a saved transcript, with
    -- each one's owner (for the plan rule) and how many sessions; at most 100. NULL when the caller
    -- is not in the server. A person who only plays has no other list of campaigns on the site.
    CREATE FUNCTION dmbot_web_transcript_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'owner', x.owner, 'sessions', x.sessions)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name, c.owner_user_id::TEXT AS owner, count(*) AS sessions
                FROM campaigns c
                JOIN transcript_sessions t
                  ON t.guild_id = c.guild_id AND t.campaign_id = c.id AND t.line_count > 0
                WHERE c.guild_id = g
                GROUP BY c.id, c.name, c.owner_user_id
                ORDER BY lower(c.name), c.id
                LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_campaigns(BIGINT) FROM PUBLIC;
"""

WEB_TRANSCRIPT_PAGES = """
    -- Transcripts on the website, part two (#1512): a size cap and the character labels.
    --
    -- dmbot_web_transcript_lines now answers one page of at most 5,000 lines, in the order the
    -- speech started (started_ms, then the row's own order, the same order as
    -- `TranscriptStore.lines`), starting after the cursor (after_ms, after_id; both NULL for the
    -- first page). The answer is {"lines": [...], "next": {"ms": ..., "id": ...} or null}: `next`
    -- is the cursor for the page after, null on the last. The same checks as before: the caller is
    -- in the server (set for the transaction, on the session's list) and the session is this
    -- campaign's, else NULL.
    DROP FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT);
    CREATE FUNCTION dmbot_web_transcript_lines(
        g BIGINT, c TEXT, sid TEXT, after_ms BIGINT, after_id BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        page_size CONSTANT INT := 5000;
        found JSONB;
        last_ms BIGINT;
        last_id BIGINT;
        more BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        -- One row more than a page, to know whether there is a next one.
        WITH page AS (
            SELECT l.*, row_number() OVER (ORDER BY l.started_ms, l.id) AS rn
            FROM (SELECT * FROM transcript_lines t
                  WHERE t.guild_id = g AND t.session_id = sid
                    AND (after_ms IS NULL OR (t.started_ms, t.id) > (after_ms, after_id))
                  ORDER BY t.started_ms, t.id
                  LIMIT page_size + 1) l
        )
        SELECT jsonb_agg(jsonb_build_object(
                   'startedMs', p.started_ms, 'userId', p.user_id::TEXT,
                   'heard', p.heard, 'text', COALESCE(p.text, p.heard),
                   'durationMs', p.duration_ms, 'topic', p.topic,
                   'entity', COALESCE(p.entity, ''), 'sidebar', COALESCE(p.sidebar, ''),
                   'ref', p.sidebar_ref, 'replyTo', p.sidebar_reply_to,
                   'via', p.sidebar_via, 'stt', p.sidebar_stt, 'model', p.sidebar_model,
                   'prompt', p.sidebar_prompt, 'sources', p.sidebar_sources,
                   'typed', p.typed) ORDER BY p.rn) FILTER (WHERE p.rn <= page_size),
               (array_agg(p.started_ms) FILTER (WHERE p.rn = page_size))[1],
               (array_agg(p.id) FILTER (WHERE p.rn = page_size))[1],
               COALESCE(bool_or(p.rn > page_size), FALSE)
          INTO found, last_ms, last_id, more
          FROM page p;
        RETURN jsonb_build_object(
            'lines', COALESCE(found, '[]'::jsonb),
            'next', CASE WHEN more THEN jsonb_build_object('ms', last_ms, 'id', last_id)
                         ELSE NULL END);
    END
    $fn$;

    -- dmbot_web_transcript_characters: who plays which character in the campaign, as a JSON object
    -- of Discord user id (text) -> the character's name, for the labels `{Cerric}` the Discord
    -- file carries. The export's rule (`cleaner.characters`, then the plays-links of
    -- `MemoryStore.table_people`), in SQL, and a little stricter so a name is never shown that
    -- the campaign's memory has not confirmed:
    --   1. a confirmed player character with a player, whose own name is a confirmed alias that is
    --      not secret; a player with more than one gets the newest (ties: the lowest id);
    --   2. for a person at the table with none of those: the last confirmed, non-secret `plays`
    --      link to a confirmed character whose own name is such an alias.
    -- A DM of the campaign never gets one (a DM may voice an NPC, and the file must not tell
    -- which). Any member of the server may ask, as for the transcripts; NULL when the caller is
    -- not in the server (or no campaign).
    CREATE FUNCTION dmbot_web_transcript_characters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(x.user_id::TEXT, x.name)
            FROM (
                WITH named AS (
                    -- every confirmed, non-secret character with a name of its own
                    SELECT e.id, e.created_at, e.role, e.played_by, a.text, a.id AS alias_id
                    FROM memory_entities e
                    JOIN memory_aliases a
                      ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id
                     AND a.entity_id = e.id
                    WHERE e.guild_id = g AND e.campaign_id = c
                      AND e.type <> 'person' AND e.status = 'confirmed'
                      AND a.status = 'confirmed' AND NOT a.secret
                      AND a.key = (SELECT n.key FROM memory_aliases n
                                   WHERE n.guild_id = g AND n.campaign_id = c
                                     AND n.entity_id = e.id AND n.text = e.name
                                     AND n.status <> 'rejected'
                                   ORDER BY n.id LIMIT 1)
                ), by_role AS (
                    SELECT DISTINCT ON (played_by) played_by AS user_id, text AS name
                    FROM named
                    WHERE role = 'player_character' AND played_by IS NOT NULL
                    ORDER BY played_by, created_at DESC, id, alias_id
                ), by_plays AS (
                    SELECT DISTINCT ON (p.played_by) p.played_by AS user_id, nm.text AS name
                    FROM memory_entities p
                    JOIN memory_relations r
                      ON r.guild_id = p.guild_id AND r.campaign_id = p.campaign_id
                     AND r.subject_id = p.id
                    JOIN (SELECT DISTINCT ON (id) id, text FROM named ORDER BY id, alias_id) nm
                      ON nm.id = r.object_id
                    WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                      AND p.status = 'confirmed' AND p.played_by IS NOT NULL
                      AND r.predicate = 'plays' AND r.status = 'confirmed' AND NOT r.secret
                      AND NOT EXISTS (SELECT 1 FROM by_role b WHERE b.user_id = p.played_by)
                    ORDER BY p.played_by, r.created_at DESC, r.id DESC
                )
                SELECT user_id, name FROM by_role
                UNION ALL
                SELECT user_id, name FROM by_plays
            ) x
            WHERE NOT EXISTS (SELECT 1 FROM campaign_dms d
                              WHERE d.guild_id = g AND d.campaign_id = c
                                AND d.user_id = x.user_id)), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_characters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_characters(BIGINT, TEXT) FROM PUBLIC;
"""

WEB_HOUSE_RULES = """
    -- A campaign's house rules on the website (#1521). They are the table's rules and every member
    -- of the Discord server may read them with /dmbot houserules (the bot does not gate that by
    -- plan), so these two doors answer any signed-in person whose session lists the server
    -- (`dmbot_web_guilds()`), with that server set for the transaction, and only read. The
    -- website's role has no grant on house_rules or rule_set_follows.
    --
    -- What leaves: each rule's number, words, the book rule it replaces and whether it was merged
    -- in from a set; the followed house-rules sets' name, version and rules (the bot cites them as
    -- "<set> house rules, vN"). NEVER a rule's scenario (the story event behind it: the Discord
    -- list does not show it), who wrote it, the session it came from, a set's publisher or share
    -- code, or the campaign's linked rules document (a private share link the bot shows only to
    -- its DMs).
    --
    -- dmbot_web_house_rules: NULL when the caller is not in the server or there is no campaign.
    CREATE FUNCTION dmbot_web_house_rules(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'rules', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'number', r.number, 'rule', r.rule, 'supersedes', r.supersedes,
                           'fromSet', r.from_set) ORDER BY r.number DESC)
                FROM house_rules r WHERE r.guild_id = g AND r.campaign_id = c), '[]'::jsonb),
            'sets', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'rules', CASE WHEN jsonb_typeof(f.content -> 'rules') = 'array'
                                         THEN f.content -> 'rules' ELSE '[]'::jsonb END)
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'house_rules'),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_house_rules_campaigns: the server's campaigns that have a house rule or follow a
    -- house-rules set, with how many of each; at most 100. NULL when the caller is not in the
    -- server. A person who only plays has no other list of campaigns on the site.
    CREATE FUNCTION dmbot_web_house_rules_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'rules', x.rules, 'sets', x.sets)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name,
                       (SELECT count(*) FROM house_rules r
                        WHERE r.guild_id = c.guild_id AND r.campaign_id = c.id) AS rules,
                       (SELECT count(*) FROM rule_set_follows f
                        WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                          AND f.kind = 'house_rules') AS sets
                FROM campaigns c
                WHERE c.guild_id = g
                  AND (EXISTS (SELECT 1 FROM house_rules r
                               WHERE r.guild_id = c.guild_id AND r.campaign_id = c.id)
                       OR EXISTS (SELECT 1 FROM rule_set_follows f
                                  WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                    AND f.kind = 'house_rules'))
                ORDER BY lower(c.name), c.id LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_rules(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_house_rules_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_house_rules(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_house_rules_campaigns(BIGINT) FROM PUBLIC;
"""

PACK_SUGGESTIONS = (
    f"""
    -- "Suggest to <pack>" (#1537): a following campaign's DM sends one of its own homebrew
    -- entries to the pack's publisher, as #1259 does for house-rules sets. Only the entry's words
    -- cross (kind, name, summary, text, whether players may know it), never who sent it, and
    -- never an entry from a book (the sender's store refuses those). The same two-table shape:
    --   pack_suggestions       in the PUBLISHER's campaign: the entry, the answer, nothing that
    --                          says which campaign, server or DM sent it
    --   pack_suggestions_sent  in the FOLLOWER's campaign: what its DM sent, to which pack, and
    --                          when it was told the answer; linked only by the random id.
    -- The doors across servers are the two functions below, like the house-rules ones; they open
    -- the tables to themselves through a setting they set and put back. The existing
    -- `suggest_door` policy on rule_sets already opens one shared set (house rules or pack) by id.
    CREATE TABLE pack_suggestions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN
            ('spell', 'monster', 'item', 'feat', 'condition', 'rule', 'other')),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        summary     TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        known       BOOLEAN NOT NULL,
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
    CREATE INDEX pack_suggestions_open ON pack_suggestions (set_id) WHERE status = 'open';
    CREATE TABLE pack_suggestions_sent (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        set_name    TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        sent_by     BIGINT NOT NULL CHECK (sent_by > 0),
        sent_at     BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("pack_suggestions")
    + _isolate("pack_suggestions_sent")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY suggest_door_read ON pack_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest''
                       AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_add ON pack_suggestions FOR INSERT TO %I
                WITH CHECK (dmbot_owner_sync() = ''suggest''
                            AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY answer_door ON pack_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggestanswer''
                       AND id = ANY(string_to_array(
                           COALESCE(current_setting(''dmbot.suggestion_ids'', true), $q$$q$),
                           $q$,$q$)))',
            current_user);
    END
    $do$;
    -- Adds a suggestion to a pack the caller's campaign follows. 'ok' (with the publisher's
    -- server and campaign, which DMbot uses to post the note and never shows anyone), 'full'
    -- (the pack already has `pack_cap` open suggestions), 'dup' (the pack already has an open one
    -- for that name), or 'gone' (the caller does not follow it, or it is not shared any more).
    CREATE FUNCTION dmbot_suggest_entry(
        sid TEXT, wanted_set TEXT, follower_campaign TEXT, new_kind TEXT, new_name TEXT,
        new_summary TEXT, new_text TEXT, new_known BOOLEAN, now_at BIGINT, pack_cap INTEGER,
        wait_s INTEGER)
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
                         AND f.set_id = wanted_set AND f.kind = 'homebrew' AND f.updating) THEN
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'suggest', true);
        PERFORM set_config('dmbot.set_id', wanted_set, true);
        SELECT s.guild_id, s.campaign_id INTO found_guild, found_campaign
            FROM rule_sets s WHERE s.id = wanted_set AND s.status = 'shared'
                               AND s.kind = 'homebrew';
        IF found_guild IS NULL THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        -- Two tables suggesting at once take turns, so the cap and the name check hold.
        PERFORM pg_advisory_xact_lock(hashtext(wanted_set));
        SELECT count(*) INTO waiting FROM pack_suggestions q
            WHERE q.set_id = wanted_set AND q.status = 'open'
              AND q.created_at > now_at - wait_s;  -- an unanswered one stops counting
        IF waiting >= pack_cap THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'full'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM pack_suggestions q
                   WHERE q.set_id = wanted_set AND q.status = 'open'
                     AND q.created_at > now_at - wait_s
                     AND lower(q.name) = lower(new_name)) THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'dup'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        INSERT INTO pack_suggestions (guild_id, campaign_id, id, set_id, kind, name, summary,
                                      text, known, status, created_at)
            VALUES (found_guild, found_campaign, sid, wanted_set, new_kind, new_name,
                    new_summary, new_text, new_known, 'open', now_at);
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_id', before_set, true);
        RETURN QUERY SELECT 'ok'::TEXT, found_guild, found_campaign;
    END
    $fn$;
    -- The answers to the pack suggestions with these ids (a comma-separated list the caller made
    -- itself): the id is the key, so nothing else of the publisher's can be reached.
    CREATE FUNCTION dmbot_pack_suggestion_answers(ids TEXT)
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
            SELECT q.id, q.status, q.version FROM pack_suggestions q
            WHERE q.id = ANY(string_to_array(ids, ','));
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.suggestion_ids', before_ids, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggest_entry('
            'TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, BIGINT, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_pack_suggestion_answers(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_suggest_entry(
        TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, BIGINT, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_pack_suggestion_answers(TEXT) FROM PUBLIC;
    """
)

TRANSCRIPT_KEEP = """
    -- Keep transcripts for (#1533; docs/PLAN.md, "Retention"): a DM chooses how long a campaign
    -- keeps its transcripts. `transcript_keep_days` is NULL (forever, today's behaviour) or 30,
    -- 90 or 365; the daily retention job deletes the lines of every session that ended longer
    -- ago than that. The session's own row stays, so session numbers never shift:
    -- `transcripts_deleted_at` marks it (the lines are gone, line_count 0).
    -- `transcript_deleted_noted` is whether the DM screen has said so once; changing the choice
    -- resets it. Nullable or defaulted columns: no existing row changes, so no row-level
    -- security needs opening.
    ALTER TABLE campaigns
        ADD COLUMN transcript_keep_days INTEGER
            CHECK (transcript_keep_days IS NULL OR transcript_keep_days IN (30, 90, 365)),
        ADD COLUMN transcript_deleted_noted BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE transcript_sessions ADD COLUMN transcripts_deleted_at BIGINT;

    -- The website lists the same sessions with the same numbers as the bot: a session whose
    -- transcript was deleted still takes its number, so the ones after it do not shift.
    CREATE OR REPLACE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'owner', camp.owner_user_id::TEXT,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines)) ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT * FROM (
                        SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                        FROM transcript_sessions t
                        WHERE t.guild_id = g AND t.campaign_id = c
                          AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                    ) numbered
                    WHERE numbered.line_count > 0
                ) s), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
"""

HOMEBREW_FROM_BOOK = """
    -- Homebrew loaded from a file after the DM said some of it is from a published book and
    -- confirmed the right to use it (#1524; CLAUDE.md, IP rule): that text stays in this one
    -- campaign and can never go into a shared ruleset, so a later pack must be able to tell it.
    -- A column with a default: no existing row changes, so no row-level security needs opening.
    ALTER TABLE homebrew_entries ADD COLUMN from_book BOOLEAN NOT NULL DEFAULT false;
    """

HOMEBREW_BOOK_EDITION = """
    -- Which rules a shared book is for (#1563): 2024, 2014 or other (a supplement), asked when a DM
    -- loads a file and says it is from a published book. It decides where the entry ranks next to
    -- the free rules (a 2014 book entry sits with the fallback, not above the 2024 book). NULL for
    -- an entry that is not from a book, and for one loaded before this (read as "other"). A
    -- nullable column with no default: no existing row changes, so no row-level security needs
    -- opening.
    ALTER TABLE homebrew_entries ADD COLUMN book_edition TEXT
        CHECK (book_edition IS NULL OR book_edition IN ('2024', '2014', 'other'));
    -- Only a shared book's entry carries an edition (the store and backups already refuse more).
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_edition_needs_book
        CHECK (book_edition IS NULL OR from_book);
    """

WEB_HOMEBREW = """
    -- A campaign's homebrew on the website (#1525). Only the entries the DM marked "players may
    -- know" leave: a hidden entry (a monster the players have not met) never leaves these doors,
    -- not even for the campaign's DM, who reads those on the DM screen in Discord. Like the house
    -- rules, any signed-in person whose session lists the server may read, with that server set
    -- for the transaction; the website's role has no grant on homebrew_entries.
    --
    -- What leaves the database: kind, name, summary, text, version, updated_at (the last two
    -- only reach the API process, for the file; the page never sends them). NEVER who wrote it
    -- or its id. (0100 also withheld whether it came from a book; 0107 now tells that, as the
    -- Discord players' card does.)
    --
    -- dmbot_web_homebrew: NULL when the caller is not in the server or there is no campaign.
    CREATE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name, 'summary', h.summary,
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at)
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c AND h.players_may_know),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_homebrew_campaigns: the server's campaigns with at least one shown entry, with
    -- how many; at most 100. NULL when the caller is not in the server.
    CREATE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'entries', x.entries)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name,
                       (SELECT count(*) FROM homebrew_entries h
                        WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                          AND h.players_may_know) AS entries
                FROM campaigns c
                WHERE c.guild_id = g
                  AND EXISTS (SELECT 1 FROM homebrew_entries h
                              WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                                AND h.players_may_know)
                ORDER BY lower(c.name), c.id LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew_campaigns(BIGINT) FROM PUBLIC;
"""

WEB_TRANSCRIPT_DELETED = """
    -- The website says why a session has no lines (#1548). A session whose transcript was deleted
    -- (a player's Delete my transcripts, or Keep transcripts for; 0101) keeps its row and number
    -- with `transcripts_deleted_at` set and line_count 0. 0101's door numbered those sessions but
    -- then dropped them from its answer; now they stay, each with `transcriptsDeletedAt`, so the
    -- list can say "Transcript deleted" instead of showing nothing. The campaigns door counts
    -- them too, so a campaign whose transcripts have all expired still shows its sessions.
    -- Replacing a door changes no row, so no row-level security needs opening; the door's checks
    -- and numbering are 0101's, and CREATE OR REPLACE drops the function's own settings, so they
    -- are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'owner', camp.owner_user_id::TEXT,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines),
                           'transcriptsDeletedAt', s.transcripts_deleted_at)
                       ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                    FROM transcript_sessions t
                    WHERE t.guild_id = g AND t.campaign_id = c
                      AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                ) s), '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_transcript_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'owner', x.owner, 'sessions', x.sessions)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name, c.owner_user_id::TEXT AS owner, count(*) AS sessions
                FROM campaigns c
                JOIN transcript_sessions t
                  ON t.guild_id = c.guild_id AND t.campaign_id = c.id
                 AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                WHERE c.guild_id = g
                GROUP BY c.id, c.name, c.owner_user_id
                ORDER BY lower(c.name), c.id
                LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_campaigns(BIGINT) FROM PUBLIC;
"""

WEB_TRANSCRIPT_KEEP = """
    -- The website says how long a campaign keeps its transcripts (#1575). `transcript_keep_days`
    -- (0101) is NULL for forever or 30, 90 or 365; both transcript doors now hand it over as
    -- `keepDays`, so a member reading the list knows the next sessions will go too and can save
    -- a copy first. It is a retention rule, not story content (#1575 decided members see it).
    -- Replacing a door changes no row, so
    -- no row-level security needs opening; the checks, numbering and plan owner are 0103's, and
    -- CREATE OR REPLACE drops the function's own settings, so they are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'owner', camp.owner_user_id::TEXT,
            'keepDays', camp.transcript_keep_days,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines),
                           'transcriptsDeletedAt', s.transcripts_deleted_at)
                       ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                    FROM transcript_sessions t
                    WHERE t.guild_id = g AND t.campaign_id = c
                      AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                ) s), '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_transcript_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'owner', x.owner, 'sessions', x.sessions,
                       'keepDays', x.keep_days)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name, c.owner_user_id::TEXT AS owner, count(*) AS sessions,
                       c.transcript_keep_days AS keep_days
                FROM campaigns c
                JOIN transcript_sessions t
                  ON t.guild_id = c.guild_id AND t.campaign_id = c.id
                 AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                WHERE c.guild_id = g
                GROUP BY c.id, c.name, c.owner_user_id, c.transcript_keep_days
                ORDER BY lower(c.name), c.id
                LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_campaigns(BIGINT) FROM PUBLIC;
"""

WEB_HOMEBREW_BOOK = """
    -- The Homebrew page tags a shared book's entries like the Discord players' card (#1579). Since
    -- #1564 the entries players may know include ones loaded from a shared rulebook (`from_book`);
    -- the card tags them [Shared book], and [Legacy 2014] when the book is for the 2014 rules and
    -- the campaign plays a newer ruleset (`book_is_legacy`, #1563). That is a rules-edition fact,
    -- not story, and legacy content is tagged wherever it appears (CLAUDE.md, "Rules edition").
    -- Still never returned: who wrote an entry, its id, the book's name (it is never kept), or an
    -- entry the DM has not marked "players may know". Replacing a door changes no row, so no
    -- row-level security needs opening; CREATE OR REPLACE drops the function's own settings, so
    -- they are set again below. The campaigns door is unchanged.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name, 'summary', h.summary,
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at,
                           'fromBook', h.from_book,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c AND h.players_may_know),
                '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
"""

WEB_HOMEBREW_PACKS = """
    -- The Homebrew page shows the packs a table follows, as the House rules page shows followed
    -- sets (#1586). A player's /dmbot rule answers from a followed pack's entries the pack marks
    -- as known to players (`known`, rules/homebrew_packs.py), so the site shows what the bot does.
    -- Per pack: its name, version and ONLY the entries marked known (kind, name, summary, text).
    -- NEVER the publisher, the share code, the publishing campaign or server, or an entry not
    -- known to players. A pack holds no book text (#1529), so there are no book tags here. The
    -- campaigns door now also counts a followed pack's known entries and says how many packs are
    -- followed. Replacing a door changes no row, so no row-level security needs opening;
    -- CREATE OR REPLACE drops the function's own settings, so they are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name, 'summary', h.summary,
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at,
                           'fromBook', h.from_book,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c AND h.players_may_know),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', t.e ->> 'kind', 'name', t.e ->> 'name',
                                          'summary', t.e -> 'summary', 'text', t.e ->> 'text')
                                          ORDER BY lower(t.e ->> 'name'), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                               WHERE jsonb_typeof(t.e) = 'object' AND t.e -> 'known' = 'true'::jsonb
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', y.id, 'name', y.name, 'entries', y.entries, 'packs', y.packs)
                       ORDER BY lower(y.name), y.id)
            FROM (
                SELECT x.*
                FROM (
                    SELECT c.id, c.name,
                           (SELECT count(*) FROM homebrew_entries h
                            WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id
                              AND h.players_may_know)
                           + (SELECT count(*)
                              FROM rule_set_follows f,
                                   jsonb_array_elements(
                                       CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                            THEN f.content -> 'entries' ELSE '[]'::jsonb END) e
                              WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                AND f.kind = 'homebrew' AND jsonb_typeof(e) = 'object'
                                AND e -> 'known' = 'true'::jsonb) AS entries,
                           (SELECT count(*) FROM rule_set_follows f
                            WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                              AND f.kind = 'homebrew') AS packs
                    FROM campaigns c
                    WHERE c.guild_id = g
                ) x
                WHERE x.entries > 0
                ORDER BY lower(x.name), x.id LIMIT 100
            ) y), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew_campaigns(BIGINT) FROM PUBLIC;
"""

HOMEBREW_ENABLED = """
    -- Turn each homebrew entry on or off for the table, instead of hiding it from players (owner
    -- decision 2026-10-16, #1629; docs/PLAN.md, "Homebrew (part 2)"). An off entry is kept, but
    -- DMbot uses it nowhere at the table (lookups, alerts, sidebar answers, the players' card,
    -- the spotter, a published pack). `enabled` has a default: every existing entry is on. The
    -- hidden flag goes (dropping a column changes no row, so no row-level security to open): a
    -- hidden entry is on from now, nothing is hidden from players any more. The website's doors
    -- below showed only the entries players may know; they show every entry now, with its state.
    ALTER TABLE homebrew_entries ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true;
    ALTER TABLE homebrew_entries DROP COLUMN players_may_know;
    -- The website's door hands over each shown entry's state too, so the page can show and
    -- change it (and its download file says `off` like the bot's). Replacing a door changes no
    -- row; CREATE OR REPLACE drops the function's own settings, so they are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name, 'summary', h.summary,
                           'text', h.text, 'version', h.version, 'updatedAt', h.updated_at,
                           'fromBook', h.from_book, 'enabled', h.enabled,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h
                WHERE h.guild_id = g AND h.campaign_id = c),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', t.e ->> 'kind', 'name', t.e ->> 'name',
                                          'summary', t.e -> 'summary', 'text', t.e ->> 'text')
                                          ORDER BY lower(t.e ->> 'name'), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                               WHERE jsonb_typeof(t.e) = 'object'
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;


    CREATE OR REPLACE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', y.id, 'name', y.name, 'entries', y.entries, 'packs', y.packs)
                       ORDER BY lower(y.name), y.id)
            FROM (
                SELECT x.*
                FROM (
                    SELECT c.id, c.name,
                           (SELECT count(*) FROM homebrew_entries h
                            WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id)
                           + (SELECT count(*)
                              FROM rule_set_follows f,
                                   jsonb_array_elements(
                                       CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                            THEN f.content -> 'entries' ELSE '[]'::jsonb END) e
                              WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                AND f.kind = 'homebrew' AND jsonb_typeof(e) = 'object'
                                ) AS entries,
                           (SELECT count(*) FROM rule_set_follows f
                            WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                              AND f.kind = 'homebrew') AS packs
                    FROM campaigns c
                    WHERE c.guild_id = g
                ) x
                WHERE x.entries > 0
                ORDER BY lower(x.name), x.id LIMIT 100
            ) y), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew_campaigns(BIGINT) FROM PUBLIC;
"""
HOMEBREW_SOURCES = (
    f"""
    -- Every homebrew entry records where it came from, and a campaign approves a site once
    -- (owner decision 2026-10-16, #1633; docs/PLAN.md, "Homebrew (part 2)"). No user-visible
    -- change here.
    -- `homebrew_pages`: ONE copy per published address. A page imported by link is public
    -- content, not a campaign's own data, so the table has no guild_id or campaign_id and no
    -- row-level security: the second exception to campaign isolation, next to shared rulesets
    -- (UNSCOPED_TABLES). It holds nothing about who imported it or where. The website's role
    -- has no grant on it; its doors read it as their owner. `fetch_status`: 'ok' (fetched), 'stale'
    -- (a later fetch failed, so the last good content is kept) or 'restored' (recreated from a
    -- backup file, so NOT trusted: no campaign relies on it until a fetch replaces it).
    CREATE TABLE homebrew_pages (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        url          TEXT NOT NULL UNIQUE CHECK (char_length(url) BETWEEN 1 AND 2000),
        domain       TEXT NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 253),
        kind         TEXT NOT NULL CHECK (kind IN ('spell', 'monster', 'item', 'feat',
                                                   'condition', 'other')),
        name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
        text         TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
        summary      TEXT CHECK (summary IS NULL OR char_length(summary) BETWEEN 1 AND 160),
        fetched_at   BIGINT NOT NULL,
        fetch_status TEXT NOT NULL DEFAULT 'ok' CHECK (fetch_status IN ('ok', 'stale', 'restored'))
    );

    -- The source of each entry: typed (the DM who typed it), upload (a file; the uploader by
    -- default, or a credit: a website, whose domain needs the same approval, or a typed name)
    -- or link (a page above; the entry's words and summary are read from the page, so they are
    -- NULL on the row). Existing entries become 'typed' by the DM who made them. The CHECK
    -- keeps each source to exactly the fields it needs.
    ALTER TABLE homebrew_entries
        ADD COLUMN source_kind   TEXT NOT NULL DEFAULT 'typed'
            CHECK (source_kind IN ('typed', 'upload', 'link')),
        ADD COLUMN added_by      BIGINT CHECK (added_by > 0),
        ADD COLUMN page_id       BIGINT REFERENCES homebrew_pages (id),
        ADD COLUMN credit_kind   TEXT CHECK (credit_kind IN ('user', 'website', 'name')),
        ADD COLUMN credit_text   TEXT CHECK (credit_text IS NULL
                                             OR char_length(credit_text) BETWEEN 1 AND 80),
        ADD COLUMN credit_domain TEXT CHECK (credit_domain IS NULL
                                             OR char_length(credit_domain) BETWEEN 1 AND 253);
    ALTER TABLE homebrew_entries ALTER COLUMN text DROP NOT NULL;
    -- Row-level security (CLAUDE.md, migrations that change rows): migrations run with no
    -- server set, so the backfill opens the table for itself and closes it again.
    CREATE POLICY migrate_backfill ON homebrew_entries USING (true) WITH CHECK (true);
    UPDATE homebrew_entries SET added_by = created_by;
    DROP POLICY migrate_backfill ON homebrew_entries;
    ALTER TABLE homebrew_entries ALTER COLUMN added_by SET NOT NULL;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_source_check CHECK (
        (source_kind = 'typed' AND page_id IS NULL AND text IS NOT NULL
            AND credit_kind IS NULL AND credit_text IS NULL AND credit_domain IS NULL)
        OR (source_kind = 'upload' AND page_id IS NULL AND text IS NOT NULL
            AND credit_kind IS NOT NULL
            AND ((credit_kind = 'user' AND credit_text IS NULL AND credit_domain IS NULL)
              OR (credit_kind = 'website' AND credit_text IS NULL AND credit_domain IS NOT NULL)
              OR (credit_kind = 'name' AND credit_text IS NOT NULL AND credit_domain IS NULL)))
        OR (source_kind = 'link' AND page_id IS NOT NULL AND text IS NULL AND summary IS NULL
            AND credit_kind IS NULL AND credit_text IS NULL AND credit_domain IS NOT NULL)
    );
    CREATE INDEX homebrew_entries_page ON homebrew_entries (page_id) WHERE page_id IS NOT NULL;

    -- A campaign approves a site once: the right-to-use warning is confirmed at the first
    -- import from it, and every later link from the same site there needs nothing more.
    -- Who approved and when is kept. Per campaign, isolated, deleted with the campaign.
    CREATE TABLE site_approvals (
        {_memory_scope()}
        domain       TEXT NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 253),
        approved_by  BIGINT NOT NULL CHECK (approved_by > 0),
        approved_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, domain)
    );
    """
    + _isolate("site_approvals")
    + """

    -- The website's homebrew door reads an imported entry's words from its page. Replacing a
    -- door changes no row; CREATE OR REPLACE drops the function's own settings, so they are
    -- set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name,
                           'summary', COALESCE(h.summary, pg.summary),
                           'text', COALESCE(h.text, pg.text), 'version', h.version,
                           'updatedAt', h.updated_at,
                           'fromBook', h.from_book, 'enabled', h.enabled,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h LEFT JOIN homebrew_pages pg ON pg.id = h.page_id
                WHERE h.guild_id = g AND h.campaign_id = c),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', t.e ->> 'kind', 'name', t.e ->> 'name',
                                          'summary', t.e -> 'summary', 'text', t.e ->> 'text')
                                          ORDER BY lower(t.e ->> 'name'), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                               WHERE jsonb_typeof(t.e) = 'object'
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;


    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
"""
)

HOMEBREW_PACK_DOORS = """
    -- The website's homebrew doors and a followed pack's imported entries (#1635; docs/PLAN.md,
    -- "Homebrew (part 2)"). A pack holds an imported entry as a pointer to its page (no words), so
    -- the door reads the words from `homebrew_pages` (never a page only a backup vouched for), and
    -- an entry from a site shows only once THIS campaign approved the site, as the bot does. The
    -- count in the campaigns door follows the same rule. Replacing a door changes no row;
    -- CREATE OR REPLACE drops the function's own settings, so they are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name,
                           'summary', COALESCE(h.summary, pg.summary),
                           'text', COALESCE(h.text, pg.text), 'version', h.version,
                           'updatedAt', h.updated_at,
                           'fromBook', h.from_book, 'enabled', h.enabled,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h LEFT JOIN homebrew_pages pg ON pg.id = h.page_id
                WHERE h.guild_id = g AND h.campaign_id = c),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', COALESCE(pg.kind, t.e ->> 'kind'),
                                          'name', COALESCE(pg.name, t.e ->> 'name'),
                                          'summary', CASE WHEN pg.id IS NOT NULL
                                                          THEN to_jsonb(pg.summary)
                                                          ELSE t.e -> 'summary' END,
                                          'text', COALESCE(pg.text, t.e ->> 'text'))
                                          ORDER BY lower(COALESCE(pg.name, t.e ->> 'name')), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                                    LEFT JOIN homebrew_pages pg
                                        ON pg.url = t.e ->> 'page' AND pg.fetch_status <> 'restored'
                               WHERE jsonb_typeof(t.e) = 'object'
                                 -- an imported entry shows only if its page is kept, and an entry
                                 -- from a site only once this campaign approved the site (#1635)
                                 AND (t.e ->> 'page' IS NULL OR pg.id IS NOT NULL)
                                 AND (t.e ->> 'domain' IS NULL OR EXISTS (
                                         SELECT 1 FROM site_approvals a
                                         WHERE a.guild_id = g AND a.campaign_id = c
                                           AND a.domain = t.e ->> 'domain'))
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;


    CREATE OR REPLACE FUNCTION dmbot_web_homebrew_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', y.id, 'name', y.name, 'entries', y.entries, 'packs', y.packs)
                       ORDER BY lower(y.name), y.id)
            FROM (
                SELECT x.*
                FROM (
                    SELECT c.id, c.name,
                           (SELECT count(*) FROM homebrew_entries h
                            WHERE h.guild_id = c.guild_id AND h.campaign_id = c.id)
                           + (SELECT count(*)
                              FROM rule_set_follows f,
                                   jsonb_array_elements(
                                       CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                            THEN f.content -> 'entries' ELSE '[]'::jsonb END) e
                              WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                                AND f.kind = 'homebrew' AND jsonb_typeof(e) = 'object'
                                AND (e ->> 'page' IS NULL OR EXISTS (
                                        SELECT 1 FROM homebrew_pages pg
                                        WHERE pg.url = e ->> 'page'
                                          AND pg.fetch_status <> 'restored'))
                                AND (e ->> 'domain' IS NULL OR EXISTS (
                                        SELECT 1 FROM site_approvals a
                                        WHERE a.guild_id = c.guild_id AND a.campaign_id = c.id
                                          AND a.domain = e ->> 'domain'))
                                ) AS entries,
                           (SELECT count(*) FROM rule_set_follows f
                            WHERE f.guild_id = c.guild_id AND f.campaign_id = c.id
                              AND f.kind = 'homebrew') AS packs
                    FROM campaigns c
                    WHERE c.guild_id = g
                ) x
                WHERE x.entries > 0
                ORDER BY lower(x.name), x.id LIMIT 100
            ) y), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew_campaigns(BIGINT) FROM PUBLIC;
"""

CLUE_AUDIENCE = """
    -- A clue only some people know (#1649; docs/STORY_MEMORY.md, section 2): the DM's switch
    -- on a `clue` entry. Who knows it is its confirmed `knows` facts, so nothing else is
    -- stored. The `clue` kind itself is core (in code), not a row. Off for every existing
    -- entry, so this migration changes no row's meaning.
    ALTER TABLE memory_entities ADD COLUMN audience_limited BOOLEAN NOT NULL DEFAULT false;
"""

STANDING = (
    f"""
    -- Story memory 5b, slice 4a (#1655; docs/STORY_MEMORY.md, section 5): standing the DM sets.
    --   memory_entities.party   the campaign's party, a group whose members are worked out in code
    --                           from its confirmed player characters. At most one a campaign.
    --   memory_impressions      how a viewer (an NPC, a group, a place) feels about a subject (a
    --                           character, the party, a group, a people): a score -100..100 and the
    --                           weight of the evidence behind it. A new one for the same pair
    --                           closes the old (closed_at), so the history is kept, like
    --                           memory_states. Sparse: a row only where the DM said something.
    --   memory_attitudes        the same for a group or place toward a whole people or kind.
    -- The `people` kind and the `of_people` connection are core (in code), not rows. Like every
    -- memory table both are one campaign's, with forced row-level security.
    ALTER TABLE memory_entities ADD COLUMN party BOOLEAN NOT NULL DEFAULT false;
    CREATE UNIQUE INDEX memory_entities_one_party
        ON memory_entities (guild_id, campaign_id) WHERE party;
    CREATE TABLE memory_impressions (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        viewer_id       TEXT NOT NULL,
        subject_id      TEXT NOT NULL,
        score           INTEGER NOT NULL CHECK (score BETWEEN -100 AND 100),
        weight          INTEGER NOT NULL CHECK (weight BETWEEN 1 AND 100),
        source          TEXT NOT NULL,
        from_session_at BIGINT,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (viewer_id <> subject_id),
        {_entity_link("viewer_id")},
        {_entity_link("subject_id")}
    );
    CREATE UNIQUE INDEX memory_impressions_one_open
        ON memory_impressions (guild_id, campaign_id, viewer_id, subject_id)
        WHERE closed_at IS NULL;
    CREATE INDEX memory_impressions_by_subject
        ON memory_impressions (guild_id, campaign_id, subject_id);
    CREATE TABLE memory_attitudes (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        holder_id       TEXT NOT NULL,
        toward_id       TEXT NOT NULL,
        score           INTEGER NOT NULL CHECK (score BETWEEN -100 AND 100),
        weight          INTEGER NOT NULL CHECK (weight BETWEEN 1 AND 100),
        source          TEXT NOT NULL,
        from_session_at BIGINT,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (holder_id <> toward_id),
        {_entity_link("holder_id")},
        {_entity_link("toward_id")}
    );
    CREATE UNIQUE INDEX memory_attitudes_one_open
        ON memory_attitudes (guild_id, campaign_id, holder_id, toward_id)
        WHERE closed_at IS NULL;
    CREATE INDEX memory_attitudes_by_target
        ON memory_attitudes (guild_id, campaign_id, toward_id);
    """
    + _isolate("memory_impressions")
    + _isolate("memory_attitudes")
)

HOMEBREW_SOURCE_DOOR = """
    -- The website's homebrew door adds where each entry came from (#1653): how it was added and the
    -- credit as the DM chose it. `added_by` and the page's address are not sent. Replacing a door
    -- changes no row; CREATE OR REPLACE drops the function's own settings, so they are set again.
    CREATE OR REPLACE FUNCTION dmbot_web_homebrew(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', h.kind, 'name', h.name,
                           'summary', COALESCE(h.summary, pg.summary),
                           'text', COALESCE(h.text, pg.text), 'version', h.version,
                           'updatedAt', h.updated_at,
                           'fromBook', h.from_book, 'enabled', h.enabled,
                           -- where it came from (#1653): the kind and the words of the credit,
                           -- never who added it (`added_by` is a Discord id: not sent)
                           'sourceKind', h.source_kind, 'creditKind', h.credit_kind,
                           'creditText', h.credit_text, 'creditDomain', h.credit_domain,
                           -- the same test as rules/book_rank.py book_is_legacy
                           'legacy', COALESCE(h.from_book AND h.book_edition = '2014'
                                              AND camp.target_ruleset <> '2014', false))
                           ORDER BY h.name_key)
                FROM homebrew_entries h LEFT JOIN homebrew_pages pg ON pg.id = h.page_id
                WHERE h.guild_id = g AND h.campaign_id = c),
                '[]'::jsonb),
            'packs', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'name', f.set_name, 'version', f.version,
                           'entries', COALESCE((
                               SELECT jsonb_agg(jsonb_build_object(
                                          'kind', COALESCE(pg.kind, t.e ->> 'kind'),
                                          'name', COALESCE(pg.name, t.e ->> 'name'),
                                          'summary', CASE WHEN pg.id IS NOT NULL
                                                          THEN to_jsonb(pg.summary)
                                                          ELSE t.e -> 'summary' END,
                                          'text', COALESCE(pg.text, t.e ->> 'text'))
                                          ORDER BY lower(COALESCE(pg.name, t.e ->> 'name')), t.ord)
                               FROM jsonb_array_elements(
                                        CASE WHEN jsonb_typeof(f.content -> 'entries') = 'array'
                                             THEN f.content -> 'entries' ELSE '[]'::jsonb END)
                                    WITH ORDINALITY AS t(e, ord)
                                    LEFT JOIN homebrew_pages pg
                                        ON pg.url = t.e ->> 'page' AND pg.fetch_status <> 'restored'
                               WHERE jsonb_typeof(t.e) = 'object'
                                 -- an imported entry shows only if its page is kept, and an entry
                                 -- from a site only once this campaign approved the site (#1635)
                                 AND (t.e ->> 'page' IS NULL OR pg.id IS NOT NULL)
                                 AND (t.e ->> 'domain' IS NULL OR EXISTS (
                                         SELECT 1 FROM site_approvals a
                                         WHERE a.guild_id = g AND a.campaign_id = c
                                           AND a.domain = t.e ->> 'domain'))
                           ), '[]'::jsonb))
                       ORDER BY f.position, f.followed_at, f.set_id)
                FROM rule_set_follows f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'homebrew'),
                '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_homebrew(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_homebrew(BIGINT, TEXT) FROM PUBLIC;
"""

STORY_THREADS = """
    -- Story threads (#1662; docs/STORY_MEMORY.md, section 2): a thread's state is kept in
    -- memory_states like a condition, so the CHECK that lists the allowed values learns the five
    -- thread states. The kinds (thread, quest, mystery, hook, goal) and the connections
    -- (in_thread, advances, resolves) are core, in code. No row changes.
    ALTER TABLE memory_states DROP CONSTRAINT memory_states_condition_check;
    ALTER TABLE memory_states ADD CONSTRAINT memory_states_condition_check CHECK (condition IN
        ('alive', 'dead', 'missing', 'captured', 'transformed', 'unknown',
         'intact', 'destroyed', 'lost',
         'hinted', 'open', 'active', 'resolved', 'abandoned'));
"""

# Homebrew gains the species, subclass and background kinds (#1663): D&D Beyond's homebrew has
# them and the owner's live check found no way to pick them. Only the kind checks widen, no row
# changes, so no migration policy is needed.
MORE_HOMEBREW_KINDS = """
    ALTER TABLE homebrew_entries DROP CONSTRAINT homebrew_entries_kind_check;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other',
                        'species', 'subclass', 'background'));
    ALTER TABLE homebrew_pages DROP CONSTRAINT homebrew_pages_kind_check;
    ALTER TABLE homebrew_pages ADD CONSTRAINT homebrew_pages_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other',
                        'species', 'subclass', 'background'));
    ALTER TABLE pack_suggestions DROP CONSTRAINT pack_suggestions_kind_check;
    ALTER TABLE pack_suggestions ADD CONSTRAINT pack_suggestions_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'rule', 'other',
                        'species', 'subclass', 'background'));
"""


OWN_WRITING = """
    -- A DM's own background, read by DMbot for names (#2167), is a different thing from a list of
    -- names (names_list) or a published adventure (shared_story): its own purpose, so the record
    -- of who confirmed what can tell them apart. No row changes.
    ALTER TABLE shared_confirmations DROP CONSTRAINT shared_confirmations_purpose_check;
    ALTER TABLE shared_confirmations ADD CONSTRAINT shared_confirmations_purpose_check
        CHECK (purpose IN ('names_list', 'shared_story', 'rulebook', 'own_writing'));
    """

RECAP_SHARE = (
    _setting("dmbot_recap_share_tok", "dmbot.recap_share_tok", "TEXT")
    + _setting("dmbot_recap_share_guild", "dmbot.recap_share_guild", "BIGINT")
    + _setting("dmbot_recap_share_campaign", "dmbot.recap_share_campaign", "TEXT")
    + """
    -- A public, read-only link for a posted recap (#2118). The token is random (256 bits,
    -- URL-safe, made by the API), stored with the recap, and is the whole key: a link finds one
    -- recap and nothing else about its campaign or server. A defaulted-NULL column changes no rows.
    ALTER TABLE recap_jobs ADD COLUMN share_token TEXT UNIQUE
        CHECK (share_token IS NULL OR share_token ~ '^[A-Za-z0-9_-]{43}$');

    -- The public read has no signed-in person and no server set, so the function that serves it
    -- needs a way past the row-level rules on the three tables it reads (the recap, and the names
    -- for the secret check). Each is a read-only policy TO the role that owns the function (the
    -- one running this migration) and tied to what the function itself sets: the recap to the
    -- token it was given, the names to the one recap's server and campaign it then found. Never the
    -- website's role: even if it set these settings itself, the policy is not for it, and a
    -- function that runs as the owner sees only that one recap and its campaign's names.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON recap_jobs FOR SELECT TO %I
                USING (share_token IS NOT NULL AND share_token = dmbot_recap_share_tok())',
            current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON memory_aliases FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())', current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON campaigns FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND id = dmbot_recap_share_campaign())', current_user);
        EXECUTE format(
            'CREATE POLICY recap_share_read ON memory_entities FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())', current_user);
    END
    $do$;

    -- dmbot_recap_shared: the posted recap a token names, or NULL for anything else (no such token,
    -- turned off, not posted): one answer. Returns the table version's words, when it was posted,
    -- where its audio is (for the API only, never sent on), and the campaign's secret and ordinary
    -- names so the API can check the text again on every read.
    CREATE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;

    -- dmbot_recap_share: a DM of the campaign turns a posted recap's link on, off, or makes a new
    -- one. `tok` is a fresh token from the API, used by "on" (when there is none) and "new".
    -- Returns {"status": "ok" | "not_posted", "token": the link's token or null}; NULL unless a DM.
    CREATE FUNCTION dmbot_recap_share(g BIGINT, c TEXT, at_ BIGINT, act TEXT, tok TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF act = 'off' THEN
            UPDATE recap_jobs SET share_token = NULL
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', NULL);
        END IF;
        IF job.status <> 'posted' THEN
            RETURN jsonb_build_object('status', 'not_posted', 'token', NULL);
        END IF;
        IF (act = 'new' OR job.share_token IS NULL)
           AND (tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$') THEN
            RETURN NULL;  -- the API always passes a good one
        END IF;
        IF act = 'new' OR job.share_token IS NULL THEN
            UPDATE recap_jobs SET share_token = tok
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', tok);
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'token', job.share_token);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_shared(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

PLAN_PENDING = """
    -- A plan change the payment company has scheduled for the end of the paid period (a downgrade
    -- or upgrade picked on its billing page, #2165): the plan that starts, and when. Filled from
    -- the company's schedule events and cleared when the change begins, is undone, or the plan
    -- ends. Two nullable columns: no rows change, so no row-level-security policy is needed.
    ALTER TABLE entitlements ADD COLUMN next_plan TEXT;
    ALTER TABLE entitlements ADD COLUMN next_plan_on BIGINT;
    -- Schedule events are ordered by their own clock, never by `last_event_at`: at a downgrade's
    -- start the schedule's "released" and the plan change arrive in the same moment, and a later
    -- schedule event must not make the real plan change look stale.
    ALTER TABLE entitlements ADD COLUMN next_plan_event_at BIGINT NOT NULL DEFAULT 0;
    -- When the person pressed Renew (our clock). Only a "cancelled" event older than this is
    -- ignored; it does not touch `last_event_at`, which is the payment company's clock alone.
    ALTER TABLE entitlements ADD COLUMN renewed_at BIGINT;
"""

# Consent given under older wording never counts for an import: the store door refuses a caller
# that passes less (the SQL says 6; a test checks the two agree and are not above TERMS_VERSION).
IMPORT_TERMS_FLOOR = 6

SESSION_IMPORTS = (
    f"""
    -- Sessions a DM loaded from a file instead of recording them (#2105). The transcript itself is
    -- an ordinary stored session (transcript_sessions and transcript_lines, from people who agreed
    -- now); this row says where it came from and how far its cleaning has got. The uploaded file
    -- is never kept. Deleted with the session and the campaign.
    CREATE TABLE session_imports (
        {_memory_scope()}
        session_id  TEXT NOT NULL,
        import_id   TEXT NOT NULL CHECK (import_id ~ '^[0-9a-f]{{32}}$'),
        file_name   TEXT NOT NULL CHECK (char_length(file_name) BETWEEN 1 AND 200),
        imported_by BIGINT NOT NULL CHECK (imported_by > 0),
        imported_at BIGINT NOT NULL,
        -- waiting: stored, not cleaned yet; working: the bot is cleaning it; done; failed.
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        reason      TEXT NOT NULL DEFAULT '' CHECK (char_length(reason) <= 40),
        claimed_at  BIGINT NOT NULL DEFAULT 0,  -- when the bot took it; an old claim is retaken
        PRIMARY KEY (guild_id, session_id),
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX session_imports_open ON session_imports (state)
        WHERE state IN ('waiting', 'working');
    CREATE INDEX session_imports_batch ON session_imports (guild_id, campaign_id, import_id);
    """
    + _isolate("session_imports")
    + f"""
    -- Who a label in an import file was, remembered per campaign so the next file's "Cerric" is
    -- suggested as the same member (#2105). Only a label (as written in a file) and an account.
    CREATE TABLE import_speakers (
        {_memory_scope()}
        label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, label)
    );
    """
    + _isolate("import_speakers")
    + _setting("dmbot_import_names", "dmbot.import_names", "TEXT")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY import_names ON web_users FOR SELECT TO %I
                USING (dmbot_import_names() = ''on'')',
            current_user);
    END
    $do$;
    -- The doors the website's role stores an import through. It has no grant on the transcript or
    -- consent tables, so each door checks the caller is a DM of the campaign in a server on the
    -- session's list, and the store door keeps only lines of members who have agreed under the
    -- terms and outside engine the caller passes (the bot's own rule, consent._select).

    -- dmbot_import_agreed: the members of the server who are recorded now (under the terms and
    -- outside engine passed) and the campaign's owner (for the plan rule). NULL unless a DM.
    CREATE FUNCTION dmbot_import_agreed(g BIGINT, c TEXT, terms INT, outside TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'agreed', COALESCE((
                SELECT jsonb_agg(x.user_id::TEXT) FROM consent x
                WHERE x.guild_id = g AND x.terms_version = terms
                  AND (outside IS NULL OR x.outside_to = outside)), '[]'::jsonb),
            'owner', (SELECT k.owner_user_id::TEXT FROM campaigns k
                      WHERE k.guild_id = g AND k.id = c));
    END
    $fn$;

    -- dmbot_import_store: one file as one stored session. `lines` is a JSON list of
    -- {ms, user, text, dur}; lines of anyone not recorded now are dropped here, whatever the
    -- caller sent. The start time moves forward a second at a time past an existing session of the
    -- campaign. Returns {session, kept, dropped} or NULL unless a DM.
    CREATE FUNCTION dmbot_import_store(
        g BIGINT, c TEXT, imp TEXT, fname TEXT, started BIGINT, lines JSONB, terms INT,
        outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        sid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        at_ BIGINT := started;
        total INT;
        kept INT;
        keep JSONB;
        who BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN NULL;
        END IF;
        IF terms < 6 OR jsonb_typeof(lines) IS DISTINCT FROM 'array' THEN
            RETURN NULL;  -- old consent never counts, whatever the caller says (IMPORT_TERMS_FLOOR)
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('import:' || g::TEXT || c, 0));
        total := jsonb_array_length(lines);
        SELECT coalesce(jsonb_agg(jsonb_build_object(
                   'ms', l.ms, 'user', l."user", 'text', l.text, 'dur', l.dur)
                   ORDER BY l.ms), '[]'),
               count(*)
            INTO keep, kept
            FROM jsonb_to_recordset(lines) AS l(ms BIGINT, "user" BIGINT, text TEXT, dur INT)
            WHERE l.text IS NOT NULL AND length(l.text) > 0
              AND l.ms BETWEEN 0 AND 10000000000 AND l.dur BETWEEN 0 AND 600000
              AND EXISTS (SELECT 1 FROM consent x
                          WHERE x.guild_id = g AND x.user_id = l."user"
                            AND x.terms_version = terms
                            AND (outside IS NULL OR x.outside_to = outside));
        IF kept = 0 THEN
            RETURN jsonb_build_object('session', NULL, 'kept', 0, 'dropped', total);
        END IF;
        WHILE EXISTS (SELECT 1 FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = c AND t.started_at = at_) LOOP
            at_ := at_ + 1;
        END LOOP;
        INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at)
            VALUES (sid, g, c, at_,
                    at_ + (SELECT coalesce(max(k.ms + k.dur), 0) / 1000
                           FROM jsonb_to_recordset(keep)
                                AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT)));
        INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard, duration_ms)
            SELECT g, sid, at_ * 1000 + k.ms, k."user", k.text, k.dur
            FROM jsonb_to_recordset(keep) AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT);
        UPDATE transcript_sessions SET line_count = kept,
            speakers = ARRAY(SELECT DISTINCT k."user"
                             FROM jsonb_to_recordset(keep)
                                  AS k(ms BIGINT, "user" BIGINT, text TEXT, dur INT) ORDER BY 1)
            WHERE id = sid;
        INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,
                                     imported_by, imported_at)
            VALUES (g, c, sid, imp, left(fname, 200), who, now_s);
        RETURN jsonb_build_object('session', sid, 'kept', kept, 'dropped', total - kept);
    END
    $fn$;


    -- dmbot_import_members: who a label can be tied to, searched by name, at most 25: the
    -- campaign's table people (each tied to a Discord account) and every other member of THIS
    -- server who is recorded now and has a name on a website account, so a new campaign has
    -- people to pick from. Never another server's people. NULL unless a DM.
    CREATE FUNCTION dmbot_import_members(g BIGINT, c TEXT, q TEXT, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found JSONB;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- web_users is closed to everyone; a policy TO the owner role opens it only while this
        -- function has set its own setting, and only this query reads names from it.
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', m.user_id::TEXT, 'name', m.name,
                       'agreed', EXISTS (SELECT 1 FROM consent x
                                         WHERE x.guild_id = g AND x.user_id = m.user_id
                                           AND x.terms_version = terms
                                           AND (outside IS NULL OR x.outside_to = outside)))
                       ORDER BY lower(m.name))
            FROM (SELECT n.name, n.user_id FROM (
                      SELECT e.name, e.played_by AS user_id, 0 AS rank
                      FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                        AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                      UNION ALL
                      SELECT u.display_name, u.user_id, 1
                      FROM web_users u
                      WHERE coalesce(u.display_name, '') <> ''
                        AND EXISTS (SELECT 1 FROM consent x
                                    WHERE x.guild_id = g AND x.user_id = u.user_id
                                      AND x.terms_version = terms
                                      AND (outside IS NULL OR x.outside_to = outside))
                        AND NOT EXISTS (SELECT 1 FROM memory_entities e2
                                        WHERE e2.guild_id = g AND e2.campaign_id = c
                                          AND e2.type = 'person' AND e2.status = 'confirmed'
                                          AND e2.played_by = u.user_id)
                  ) n
                  WHERE (q = '' OR position(lower(q) IN lower(n.name)) > 0)
                  ORDER BY (lower(n.name) = lower(q)) DESC, n.rank, lower(n.name)
                  LIMIT 25) m), '[]'::jsonb) INTO found;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;

    -- dmbot_import_progress: the sessions of one import and their state. NULL unless a DM.
    CREATE FUNCTION dmbot_import_progress(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', i.file_name, 'state', i.state, 'reason', i.reason,
                       'date', t.started_at) ORDER BY t.started_at, i.session_id)
            FROM session_imports i JOIN transcript_sessions t
              ON t.guild_id = i.guild_id AND t.id = i.session_id
            WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp), '[]'::jsonb);
    END
    $fn$;

    -- dmbot_import_mapping: the remembered {label: member} of the campaign, and (with `put`) the
    -- new choices to remember, kept only for members recorded now. NULL unless a DM.
    CREATE FUNCTION dmbot_import_mapping(
        g BIGINT, c TEXT, put JSONB, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF put IS NOT NULL AND jsonb_typeof(put) = 'object' THEN
            INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)
                SELECT g, c, left(p.key, 80), p.value::BIGINT, now_s
                FROM jsonb_each_text(put) AS p(key, value)
                WHERE p.key <> '' AND p.value ~ '^[0-9]{1,19}$'
                  AND (length(p.value) < 19 OR p.value <= '9223372036854775807')
                  AND p.value::BIGINT > 0
                  AND terms >= 6  -- old consent never counts (IMPORT_TERMS_FLOOR)
                  AND EXISTS (SELECT 1 FROM consent x  -- only people recorded now are remembered
                              WHERE x.guild_id = g AND x.user_id = p.value::BIGINT
                                AND x.terms_version = terms
                                AND (outside IS NULL OR x.outside_to = outside))
                  AND (SELECT count(*) FROM import_speakers s2
                       WHERE s2.guild_id = g AND s2.campaign_id = c) < 200
                ON CONFLICT (guild_id, campaign_id, label)
                DO UPDATE SET user_id = EXCLUDED.user_id, updated_at = EXCLUDED.updated_at;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(s.label, s.user_id::TEXT)
            FROM import_speakers s WHERE s.guild_id = g AND s.campaign_id = c), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_store(BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_agreed(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_store(
        BIGINT, TEXT, TEXT, TEXT, BIGINT, JSONB, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_import_mapping(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
    """
)

MEMBER_DIRECTORY = (
    """
    -- Who a name in an imported transcript can be tied to (#2141): the members of a server who
    -- agreed to be recorded, with the display name and Discord username the bot last saw. Their
    -- names already appear in the server's shared transcripts, so this keeps nothing new about
    -- them. It is kept per server (never read across servers), written only by the bot, and a row
    -- is dropped when the person stops being recorded. The import search shows a row only while
    -- the person's consent is current, so a stale row is never offered.
    CREATE TABLE member_directory (
        guild_id     BIGINT NOT NULL,
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        display_name TEXT NOT NULL DEFAULT '' CHECK (char_length(display_name) <= 100),
        username     TEXT NOT NULL DEFAULT '' CHECK (char_length(username) <= 100),
        updated_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("member_directory")
    + """
    -- The import page's member search, now also over the directory: the campaign's people first,
    -- then the server's recorded members by name or username, at most 25. A name from the
    -- directory never beats a campaign person who plays the same account. NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_members(
        g BIGINT, c TEXT, q TEXT, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found JSONB;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', m.user_id::TEXT, 'name', m.name,
                       'agreed', EXISTS (SELECT 1 FROM consent x
                                         WHERE x.guild_id = g AND x.user_id = m.user_id
                                           AND x.terms_version = terms
                                           AND (outside IS NULL OR x.outside_to = outside)))
                       ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name))
            FROM (SELECT one.name, one.user_id, one.rank FROM (
                      -- One row for each account: the campaign's name for the person if there is
                      -- one, else the directory's, else the website's. A search matches any of
                      -- the account's names, so it finds the same person whichever it hits.
                      SELECT n.user_id,
                             (array_agg(n.name ORDER BY n.rank, lower(n.name)))[1] AS name,
                             min(n.rank) AS rank,
                             lower(string_agg(n.hay, ' ')) AS hay
                      FROM (
                          SELECT e.played_by AS user_id, e.name, 0 AS rank, e.name AS hay
                          FROM memory_entities e
                          WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                            AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                          UNION ALL
                          SELECT d.user_id,
                                 CASE WHEN d.display_name <> '' THEN d.display_name
                                      ELSE d.username END,
                                 1, d.display_name || ' ' || d.username
                          FROM member_directory d
                          WHERE d.guild_id = g AND (d.display_name <> '' OR d.username <> '')
                          UNION ALL
                          SELECT u.user_id, u.display_name, 2, u.display_name
                          FROM web_users u
                          WHERE coalesce(u.display_name, '') <> ''
                      ) n
                      WHERE EXISTS (SELECT 1 FROM consent x
                                    WHERE x.guild_id = g AND x.user_id = n.user_id
                                      AND x.terms_version = terms
                                      AND (outside IS NULL OR x.outside_to = outside))
                         OR n.rank = 0
                      GROUP BY n.user_id
                  ) one
                  WHERE q = '' OR position(lower(q) IN one.hay) > 0
                  ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)
                  LIMIT 25) m), '[]'::jsonb) INTO found;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;
    """
)
IMPORT_SUGGEST = r"""
    -- The import page's suggestions (#2135): for each name in a file, who it probably is and which
    -- character they play. Read-only, never decides anything: the DM confirms. `wanted` is a
    -- list of {label, user, shown, member}: `user` is the username part of Craig's
    -- "Display (username)", `shown` the display part, `member` a person already known for the
    -- label (remembered from an earlier import). A person is looked for by username, then by
    -- display name or server nickname, then by the names the campaign's table already uses for
    -- them; the first step that finds anyone decides, and two people at that step suggest no one.
    -- Members come from the server's directory (only while their consent is current) and the
    -- campaign's own people. Characters: the one they already play, else a confirmed character
    -- called what the label is called. Never another server's or campaign's names. NULL unless a
    -- DM of the campaign.
    CREATE FUNCTION dmbot_import_suggest(g BIGINT, c TEXT, wanted JSONB, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out JSONB := '{}'::jsonb;
        w JSONB;
        u TEXT;
        s TEXT;
        lbl TEXT;
        uid BIGINT;
        who TEXT;
        why TEXT;
        found BIGINT[];
        agreed BOOLEAN;
        plays TEXT;
        plays_why TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF wanted IS NULL OR jsonb_typeof(wanted) <> 'array' THEN
            RETURN out;
        END IF;
        FOR w IN SELECT e FROM jsonb_array_elements(wanted) WITH ORDINALITY AS t(e, n)
                 WHERE t.n <= 100 LOOP
            lbl := coalesce(w->>'label', '');
            u := lower(btrim(left(coalesce(w->>'user', ''), 100)));
            s := lower(btrim(left(coalesce(w->>'shown', ''), 100)));
            uid := NULL; who := NULL; why := NULL; plays := NULL; plays_why := NULL;
            found := NULL;
            IF lbl = '' THEN
                CONTINUE;
            END IF;
            IF coalesce(w->>'member', '') ~ '^[0-9]{1,19}$'
               AND (length(w->>'member') < 19 OR w->>'member' <= '9223372036854775807')
               AND (w->>'member')::BIGINT > 0
               AND (EXISTS (SELECT 1 FROM member_directory d
                            WHERE d.guild_id = g AND d.user_id = (w->>'member')::BIGINT)
                    OR EXISTS (SELECT 1 FROM memory_entities p
                               WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                                 AND p.played_by = (w->>'member')::BIGINT)
                    OR EXISTS (SELECT 1 FROM consent x
                               WHERE x.guild_id = g AND x.user_id = (w->>'member')::BIGINT))
            THEN
                uid := (w->>'member')::BIGINT;  -- remembered from an earlier import
                why := 'remembered';
                who := '';
            ELSE
                -- 1. the username part
                IF u <> '' THEN
                    SELECT array_agg(d.user_id) INTO found FROM member_directory d
                        WHERE d.guild_id = g AND lower(d.username) = u
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = d.user_id
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF found IS NOT NULL THEN
                        why := 'username';
                    END IF;
                END IF;
                -- 2. the display name or server nickname
                IF found IS NULL AND (s <> '' OR u <> '') THEN
                    SELECT array_agg(d.user_id) INTO found FROM member_directory d
                        WHERE d.guild_id = g AND d.display_name <> ''
                          AND lower(d.display_name) IN (s, u)
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = d.user_id
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF found IS NOT NULL THEN
                        why := 'display_name';
                    END IF;
                END IF;
                -- 3. the names the table already uses for a person
                IF found IS NULL AND s <> '' THEN
                    SELECT array_agg(DISTINCT p.played_by) INTO found FROM memory_entities p
                        WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                          AND p.status = 'confirmed' AND p.played_by IS NOT NULL
                          AND NOT p.audience_limited
                          AND EXISTS (SELECT 1 FROM memory_aliases a
                                      WHERE a.guild_id = g AND a.campaign_id = c
                                        AND a.entity_id = p.id AND a.status = 'confirmed'
                                        AND NOT a.secret AND lower(a.text) = s
                                        AND (lower(p.name) <> s OR a.key = (
                                             SELECT n.key FROM memory_aliases n
                                             WHERE n.guild_id = g AND n.campaign_id = c
                                               AND n.entity_id = p.id AND n.text = p.name
                                               AND n.status <> 'rejected'
                                             ORDER BY n.id LIMIT 1)));
                    IF found IS NOT NULL THEN
                        why := 'people';
                    END IF;
                END IF;
                IF found IS NOT NULL AND cardinality(found) = 1 THEN
                    uid := found[1];
                    found := NULL;
                ELSE
                    why := NULL;  -- nobody, or more than one at the deciding step: the DM picks
                END IF;
                IF uid IS NOT NULL THEN
                    -- The server's own name for them only while they are recorded now; else the
                    -- campaign's name for the person.
                    SELECT coalesce(nullif(d.display_name, ''), d.username) INTO who
                        FROM member_directory d
                        WHERE d.guild_id = g AND d.user_id = uid
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = uid
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF who IS NULL THEN
                        SELECT p.name INTO who FROM memory_entities p
                            WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                              AND p.status = 'confirmed' AND p.played_by = uid
                              AND NOT p.audience_limited
                              AND EXISTS (SELECT 1 FROM memory_aliases a
                                          WHERE a.guild_id = g AND a.campaign_id = c
                                            AND a.entity_id = p.id AND a.text = p.name
                                            AND a.status = 'confirmed' AND NOT a.secret
                                            AND a.key = (SELECT n.key FROM memory_aliases n
                                                         WHERE n.guild_id = g AND n.campaign_id = c
                                                           AND n.entity_id = p.id
                                                           AND n.text = p.name
                                                           AND n.status <> 'rejected'
                                                         ORDER BY n.id LIMIT 1))
                            ORDER BY p.created_at, p.id LIMIT 1;
                    END IF;
                    who := coalesce(who, '');
                END IF;
            END IF;
            -- The character: the one they already play, else one called what the label is.
            IF uid IS NOT NULL THEN
                SELECT ch.name INTO plays
                    FROM memory_relations r
                    JOIN memory_entities p ON p.guild_id = r.guild_id
                         AND p.campaign_id = r.campaign_id AND p.id = r.subject_id
                    JOIN memory_entities ch ON ch.guild_id = r.guild_id
                         AND ch.campaign_id = r.campaign_id AND ch.id = r.object_id
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'plays'
                      AND r.status = 'confirmed' AND r.to_session_at IS NULL AND NOT r.secret
                      AND p.type = 'person' AND p.played_by = uid AND p.status = 'confirmed'
                      AND ch.status = 'confirmed' AND ch.type = 'character'
                      AND NOT ch.audience_limited
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = ch.id AND a.text = ch.name
                                    AND a.status = 'confirmed' AND NOT a.secret
                                    AND a.key = (SELECT n.key FROM memory_aliases n
                                                 WHERE n.guild_id = g AND n.campaign_id = c
                                                   AND n.entity_id = ch.id AND n.text = ch.name
                                                   AND n.status <> 'rejected'
                                                 ORDER BY n.id LIMIT 1))
                    ORDER BY ch.created_at DESC, ch.id LIMIT 1;
                IF plays IS NOT NULL THEN
                    plays_why := 'plays';
                END IF;
            END IF;
            IF plays IS NULL AND s <> '' THEN
                SELECT ch.name INTO plays FROM memory_entities ch
                    WHERE ch.guild_id = g AND ch.campaign_id = c AND ch.type = 'character'
                      AND ch.status = 'confirmed' AND ch.played_by IS NULL
                      AND lower(ch.name) = s
                      AND NOT ch.audience_limited
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = ch.id AND a.text = ch.name
                                    AND a.status = 'confirmed' AND NOT a.secret
                                    AND a.key = (SELECT n.key FROM memory_aliases n
                                                 WHERE n.guild_id = g AND n.campaign_id = c
                                                   AND n.entity_id = ch.id AND n.text = ch.name
                                                   AND n.status <> 'rejected'
                                                 ORDER BY n.id LIMIT 1))
                    ORDER BY ch.created_at, ch.id LIMIT 1;
                IF plays IS NOT NULL THEN
                    plays_why := 'same_name';
                END IF;
            END IF;
            agreed := uid IS NOT NULL AND EXISTS (
                SELECT 1 FROM consent x
                WHERE x.guild_id = g AND x.user_id = uid AND x.terms_version = terms
                  AND (outside IS NULL OR x.outside_to = outside));
            out := out || jsonb_build_object(lbl, jsonb_build_object(
                'member', CASE WHEN uid IS NULL THEN NULL ELSE jsonb_build_object(
                    'userId', uid::TEXT, 'name', who, 'why', why, 'agreed', agreed) END,
                'character', CASE WHEN plays IS NULL THEN NULL ELSE jsonb_build_object(
                    'name', plays, 'why', plays_why) END));
        END LOOP;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
"""

IMPORT_PLAYS = (
    f"""
    -- Who plays which character, chosen by the DM on the website's import page (#2135 part B).
    -- The website's role cannot write story memory, so the choice waits here, one per member and
    -- campaign, and the bot makes it a DM-confirmed link (the names panel's own path) in its
    -- import round, then deletes the row. A wait, not content: left out of backups.
    CREATE TABLE import_plays (
        {_memory_scope()}
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        character_id TEXT NOT NULL,
        created_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, user_id)
    );
    """
    + _isolate("import_plays")
    + r"""
    -- dmbot_import_plays: stores the DM's choices, [{"user": "<member id>", "name": "<character>"}]
    -- (at most 100). NULL unless the caller is a DM of the campaign. A choice is kept only for a
    -- member recorded now, and for one confirmed character with that name that nobody plays, is
    -- not limited to some players, and whose own name is a confirmed, non-secret one (the rule
    -- the transcript export uses). Returns how many were kept.
    CREATE FUNCTION dmbot_import_plays(g BIGINT, c TEXT, wanted JSONB, terms INT, outside TEXT)
    RETURNS INT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        w JSONB;
        uid BIGINT;
        found TEXT[];
        kept INT := 0;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF wanted IS NULL OR jsonb_typeof(wanted) <> 'array' OR terms < 6 THEN
            RETURN 0;
        END IF;
        FOR w IN SELECT e FROM jsonb_array_elements(wanted) WITH ORDINALITY AS t(e, n)
                 WHERE t.n <= 100 LOOP
            IF jsonb_typeof(w) <> 'object'
               OR coalesce(w->>'user', '') !~ '^[0-9]{1,19}$'
               OR (length(w->>'user') = 19 AND w->>'user' > '9223372036854775807')
               OR (w->>'user')::BIGINT <= 0
               OR btrim(coalesce(w->>'name', '')) = '' THEN
                CONTINUE;
            END IF;
            uid := (w->>'user')::BIGINT;
            IF NOT EXISTS (SELECT 1 FROM consent x
                           WHERE x.guild_id = g AND x.user_id = uid AND x.terms_version = terms
                             AND (outside IS NULL OR x.outside_to = outside)) THEN
                CONTINUE;
            END IF;
            -- A name shared by two live characters (played, limited or not) is the DM's to pick
            -- in Discord: the free one is not guessed.
            IF (SELECT count(*) FROM memory_entities t
                WHERE t.guild_id = g AND t.campaign_id = c AND t.type = 'character'
                  AND t.status IN ('proposed', 'confirmed')
                  AND lower(t.name) = lower(btrim(left(w->>'name', 100)))) <> 1 THEN
                CONTINUE;
            END IF;
            SELECT array_agg(ch.id) INTO found FROM memory_entities ch
                WHERE ch.guild_id = g AND ch.campaign_id = c AND ch.type = 'character'
                  AND ch.status = 'confirmed' AND ch.played_by IS NULL
                  AND lower(ch.name) = lower(btrim(left(w->>'name', 100)))
                  AND NOT ch.audience_limited
                  AND EXISTS (SELECT 1 FROM memory_aliases a
                              WHERE a.guild_id = g AND a.campaign_id = c
                                AND a.entity_id = ch.id AND a.text = ch.name
                                AND a.status = 'confirmed' AND NOT a.secret
                                AND a.key = (SELECT n.key FROM memory_aliases n
                                             WHERE n.guild_id = g AND n.campaign_id = c
                                               AND n.entity_id = ch.id AND n.text = ch.name
                                               AND n.status <> 'rejected'
                                             ORDER BY n.id LIMIT 1));
            IF found IS NULL OR cardinality(found) <> 1 THEN
                CONTINUE;  -- none, or two with one name: the DM picks it in Discord
            END IF;
            INSERT INTO import_plays (guild_id, campaign_id, user_id, character_id, created_at)
                VALUES (g, c, uid, found[1], now_s)
                ON CONFLICT (guild_id, campaign_id, user_id)
                DO UPDATE SET character_id = EXCLUDED.character_id,
                              created_at = EXCLUDED.created_at;
            kept := kept + 1;
        END LOOP;
        RETURN kept;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
    """
)

HOUSE_RULE_WINS = f"""
    -- Which of two house rules that disagree wins (#2284), decided by the DM. A side is a rule
    -- of the campaign's own (`*_set` empty) or a rule of a set it follows (the set's name and
    -- the rule's number in it). The pair is stored once, the smaller side first, and `winner`
    -- says which side wins. Per campaign, deleted with it, carried in backups.
    CREATE TABLE house_rule_wins (
        {_memory_scope()}
        a_set       TEXT NOT NULL DEFAULT '',
        a_number    INTEGER NOT NULL CHECK (a_number > 0),
        b_set       TEXT NOT NULL DEFAULT '',
        b_number    INTEGER NOT NULL CHECK (b_number > 0),
        winner      TEXT NOT NULL CHECK (winner IN ('a', 'b')),
        decided_by  BIGINT NOT NULL,
        decided_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, a_set, a_number, b_set, b_number),
        CHECK (a_set COLLATE "C" < b_set COLLATE "C"
               OR (a_set = b_set AND a_number < b_number))
    );
    """ + _isolate("house_rule_wins")

HOUSE_RULE_SOURCE = """
    -- Where each house rule came from (#2285; docs/PLAN.md, "Seeing where house rules come from"):
    -- 'discord' is every rule from before this was recorded ("added in Discord"), 'typed' the
    -- Add form, 'voice' a rule said at the table, 'alert' one made from a rules card, 'linked' one
    -- accepted from the linked file, 'upload' one accepted from a file the DM uploaded (whose name
    -- is `source_name`). New columns with defaults: no row is rewritten, so no row-level security
    -- to open.
    ALTER TABLE house_rules
        ADD COLUMN source TEXT NOT NULL DEFAULT 'discord'
            CHECK (source IN ('discord', 'typed', 'voice', 'alert', 'linked', 'upload')),
        ADD COLUMN source_name TEXT
            CHECK (source_name IS NULL OR char_length(source_name) BETWEEN 1 AND 200);
"""

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
HIDDEN_FROM_PLAYERS = """
    -- A name the players don't know yet (#2321): the DM's switch on any entry ("Players don't
    -- know this one yet"), apart from a clue's audience (`audience_limited`, who knows a clue),
    -- which keeps its own meaning. Off for every existing entry, so no row changes; the column
    -- has a default and is added, not updated, so no row-level security needs opening.
    ALTER TABLE memory_entities ADD COLUMN hidden_from_players BOOLEAN NOT NULL DEFAULT false;

    -- A player's character is named to everyone (the transcript file's labels, the table list),
    -- so it can never be hidden: whichever path makes an entry a player's character (an import,
    -- a change of kind, the "plays" answer, a merge) clears the flag in the same write. One
    -- trigger covers them all, the SQL functions that set `played_by` included. It only edits
    -- the row being written, so it needs no extra rights.
    CREATE FUNCTION dmbot_unhide_player_characters() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.hidden_from_players AND (
            NEW.played_by IS NOT NULL OR NEW.party OR NEW.role = 'player_character'
        ) THEN
            NEW.hidden_from_players := false;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER memory_entities_unhide_players
        BEFORE INSERT OR UPDATE ON memory_entities
        FOR EACH ROW EXECUTE FUNCTION dmbot_unhide_player_characters();

    -- The two recap functions that list the names a recap's text is checked against, and the
    -- names of a shared recap page, now count such an entry as secret. CREATE OR REPLACE resets
    -- a function's own settings, so each one's search_path is set again below.
    CREATE OR REPLACE FUNCTION dmbot_recap_check_names(g BIGINT, c TEXT, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb),
            'recorded', COALESCE((
                SELECT jsonb_agg(c2.user_id) FROM consent c2
                WHERE c2.guild_id = g AND c2.terms_version = terms
                  AND (outside IS NULL OR c2.outside_to = outside)), '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

RECAP_HOLDS = f"""
    -- A session's recap held until the DM has answered DMbot's name questions (#2169). A hold is
    -- a row here, not a job state: the recap job is made when the hold is released (the last
    -- question answered, or "Write the recap now"), so the job list and the workers do not
    -- change and nothing costs anything while it waits. One per session. Per campaign, isolated,
    -- deleted with the campaign, and left out of backups (it is a wait, not content).
    CREATE TABLE recap_holds (
        {_memory_scope()}
        session_at  BIGINT NOT NULL,
        session_id  TEXT NOT NULL,
        dm_user_id  BIGINT NOT NULL CHECK (dm_user_id > 0),
        channel_id  BIGINT NOT NULL,
        created_at  BIGINT NOT NULL,
        -- The DM has been told on the DM screen. A hold made while the story extractor still had
        -- names to propose is silent until it is done: there may be nothing to ask.
        asked       BOOLEAN NOT NULL DEFAULT TRUE,
        PRIMARY KEY (guild_id, campaign_id, session_at)
    );
    """ + _isolate("recap_holds")

RECAP_HOLD_NAMES = """
    -- How many name questions a held recap waits on (#2174), written by the bot each time it counts
    -- (at the hold and in every round), so the website can say "N answers to give" without
    -- reading memory. A defaulted column changes no rows, so no row-level-security policy is
    -- needed. The website reads it only through the door below.
    ALTER TABLE recap_holds ADD COLUMN names_open INTEGER NOT NULL DEFAULT 0
        CHECK (names_open >= 0);

    -- The signed-in person's held recaps in one server: only campaigns they are a DM of, only
    -- holds the DM was told about and that still wait on an answer (a silent hold is waiting for
    -- the story extractor, and one with nothing left is released by the next round). Counts and
    -- times only; no name or text. The server must be one of the session's, as for the other
    -- website doors (a second layer: forced row-level security already holds the same line).
    CREATE FUNCTION dmbot_web_recap_holds(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN '[]'::jsonb;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'campaignId', h.campaign_id, 'campaign', c.name,
                       'sessionAt', h.session_at, 'namesOpen', h.names_open)
                   ORDER BY h.session_at DESC)
            FROM recap_holds h
            JOIN campaigns c ON c.guild_id = h.guild_id AND c.id = h.campaign_id
            WHERE h.guild_id = g AND h.asked AND h.names_open > 0
              AND EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = h.guild_id AND d.campaign_id = h.campaign_id
                            AND d.user_id = dmbot_current_user())), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_holds(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_recap_holds(BIGINT) FROM PUBLIC;
"""

RECAP_HOLD_REMINDED = """
    -- The one reminder a held recap gets after a day (#2174): set when it is claimed, so the
    -- round and a restart never send it twice. A defaulted column changes no rows, so no
    -- row-level-security policy is needed.
    ALTER TABLE recap_holds ADD COLUMN reminded BOOLEAN NOT NULL DEFAULT FALSE;
"""

IMPORT_RECAPS = """
    -- Recaps of imported sessions (#2106): the DM's per-session "Write recaps" choice, and where
    -- the bot has got with the session's name scan. `scan_from`/`scan_to` are the seconds in
    -- which that scan proposed its names, so the recap can wait for the questions from THIS
    -- session only (a live session counts by its own start time; an old one has none).
    -- Defaulted columns change no rows, so no row-level-security policy is needed.
    ALTER TABLE session_imports
        ADD COLUMN write_recap BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN scanned     BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN scan_from   BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN scan_to     BIGINT NOT NULL DEFAULT 0,
        -- failed tries of the scan (it is given up on after a few), and whether the recap for
        -- this session has been dealt with (made, held, or decided against).
        ADD COLUMN scan_tries  INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN recap_started BOOLEAN NOT NULL DEFAULT FALSE,
        -- `scanned` is the claim (scan_from is when it was taken); a claim that never finished
        -- (the bot stopped) is taken again after a while.
        ADD COLUMN scan_finished BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN recap_tries INTEGER NOT NULL DEFAULT 0;
    -- The holds of one import act as a group (one question, one reminder, one expiry line, one
    -- "Write the recap now"): they share the import's id. Empty for a live session's hold.
    ALTER TABLE recap_holds ADD COLUMN group_id TEXT NOT NULL DEFAULT '';

    -- dmbot_import_write_recap: set the choice of one stored import, only while it waits to be
    -- cleaned and only by a DM of the campaign in a server on the session's list. True when set.
    CREATE FUNCTION dmbot_import_write_recap(g BIGINT, c TEXT, sid TEXT, on_ BOOLEAN)
    RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN FALSE;
        END IF;
        UPDATE session_imports SET write_recap = on_
            WHERE guild_id = g AND campaign_id = c AND session_id = sid AND state = 'waiting';
        RETURN FOUND;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

IMPORT_PROGRESS = """
    -- A scan let go after its tries (#2106 step 3) is remembered, so the progress call can tell the
    -- DM "couldn't check names for this session" instead of showing a clean finish. The column is
    -- added with a default, so no row changes and no backfill policy is needed.
    ALTER TABLE session_imports ADD COLUMN scan_gave_up BOOLEAN NOT NULL DEFAULT FALSE;

    -- dmbot_import_progress: each session of one import with where it has got to (`stage`), the
    -- name questions its recap waits on, and whether its name check was given up. NULL unless a DM.
    -- Replaces the 0079 function; the stage reads the hold and the recap job of the same session.
    CREATE OR REPLACE FUNCTION dmbot_import_progress(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', i.file_name, 'state', i.state, 'reason', i.reason,
                       'date', t.started_at,
                       'namesUnchecked', i.scan_gave_up,
                       'namesToAnswer', CASE WHEN h.asked THEN h.names_open ELSE 0 END,
                       'stage', CASE
                           WHEN i.state = 'failed' THEN 'failed'
                           WHEN i.state <> 'done' THEN 'cleaning'
                           WHEN NOT i.scan_finished THEN 'checking_names'
                           WHEN NOT i.write_recap THEN 'done'
                           WHEN h.session_at IS NOT NULL AND NOT h.asked THEN 'finding_names'
                           WHEN h.session_at IS NOT NULL THEN 'waiting_for_names'
                           WHEN r.state = 'ready' THEN 'recap_ready'
                           WHEN r.state = 'failed' THEN 'recap_failed'
                           WHEN r.session_at IS NOT NULL THEN 'writing_recap'
                           WHEN i.recap_started THEN 'recap_not_written'
                           ELSE 'starting_recap' END)
                   ORDER BY t.started_at, i.session_id)
            FROM session_imports i
            JOIN transcript_sessions t ON t.guild_id = i.guild_id AND t.id = i.session_id
            LEFT JOIN recap_holds h ON h.guild_id = i.guild_id AND h.campaign_id = i.campaign_id
                AND h.session_at = t.started_at
            LEFT JOIN recap_jobs r ON r.guild_id = i.guild_id AND r.campaign_id = i.campaign_id
                AND r.session_at = t.started_at
            WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""

OLD_SPELLING = """
    -- A fixed spelling keeps the old one as an "old spelling" other name (#2170): the cleaner
    -- still matches it, so a line that says it is fixed to the new spelling and a search finds
    -- the entry, but speech-to-text is never told to listen for it.
    ALTER TABLE memory_aliases DROP CONSTRAINT memory_aliases_kind_check;
    ALTER TABLE memory_aliases ADD CONSTRAINT memory_aliases_kind_check
        CHECK (kind IN ('full', 'short', 'nickname', 'title', 'misheard', 'old'));

    -- The website's "said as" list and write door leave old spellings out too: nothing is spoken
    -- from them, so a spelling set on one would be dead data. The same two functions as
    -- WEB_NAMES with that one condition changed; replacing a function resets its search_path, so
    -- it is set again. The grants stay.
    """ + _WEB_NAMES_FUNCTIONS.replace("a.kind <> 'misheard'", "a.kind NOT IN ('misheard', 'old')")

TRANSCRIPT_RESPELLS = """
    -- Where a fixed name's old spelling was written as the new one in past cleaned transcripts
    -- (#2170), so one Undo can put it back. Positions only: the character places in the cleaned
    -- line and the name as it was written there; never the words around it, so a line a person
    -- later asks DMbot to forget leaves nothing behind here. Keyed by the memory change that
    -- fixed the spelling (`batch`), and by `run`: a big campaign is done in runs of at most 5000
    -- lines, each with its own Undo. Rows go with their campaign and their session. New table:
    -- no rows change, so no backfill policy.
    CREATE TABLE transcript_respells (
        id          BIGSERIAL PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        batch       BIGINT NOT NULL,
        session_id  TEXT NOT NULL,
        line_id     BIGINT NOT NULL REFERENCES transcript_lines (id) ON DELETE CASCADE,
        run         INTEGER NOT NULL DEFAULT 1,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        old_text    TEXT NOT NULL CHECK (char_length(old_text) BETWEEN 1 AND 200),
        new_text    TEXT NOT NULL CHECK (char_length(new_text) BETWEEN 1 AND 200),
        offsets     INTEGER[] NOT NULL,
        was         TEXT[] NOT NULL,
        made_at     BIGINT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_respells_batch ON transcript_respells (campaign_id, batch, run);
    """ + _isolate("transcript_respells")

RECAP_RESPELLED = """
    -- When a fixed name's spelling changed a recap's text (#2170, part C): the site shows the DM a
    -- small "spelling updated" note on it, most of all on a posted recap, whose Discord message is
    -- not changed. NULL: never. A new nullable column: no rows change, so no backfill policy.
    ALTER TABLE recap_jobs ADD COLUMN respelled_at BIGINT;
    """

RECAP_REQUESTS = (
    f"""
    -- What a DM types on the recap page to fix a recap (#2113). The website has no AI key, so it
    -- only queues the words here; the bot sorts them (the same sort as the DM screen's) and
    -- writes the points back on this row, which the page polls. The words are the DM's own about
    -- one recap and live here for an hour at most; they are never logged. `answer` holds the
    -- sorted points. Per campaign, isolated, gone with the campaign, left out of backups.
    CREATE TABLE recap_requests (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        session_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        kind        TEXT NOT NULL CHECK (kind IN ('instructions')),
        body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 1000),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        answer      JSONB,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX recap_requests_open ON recap_requests (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("recap_requests")
    + r"""
    -- The doors the website's role uses: it has no grant on the table. Each checks the caller is
    -- a DM of the campaign in a server on the session's list.
    CREATE FUNCTION dmbot_recap_request_add(g BIGINT, c TEXT, at_ BIGINT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- The one-hour promise holds for a server the bot has left too: old words go here.
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'instructions', btrim(txt), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- A request's state and sorted points, to the DM who made it and no one else.
    CREATE FUNCTION dmbot_recap_request_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer);
    END
    $fn$;

    -- "Rewrite with these": the points the DM kept go to the recap's next try (corrections, for
    -- this recap only) and to the campaign's standing notes (style, at most 10, the oldest
    -- dropped), the recap goes back to the queue with one more try, and the approval ends.
    -- Only a request of the caller's own that is done; ids must be ones it offered.
    CREATE FUNCTION dmbot_recap_rewrite(
        g BIGINT, c TEXT, at_ BIGINT, rid TEXT, keep_c TEXT[], keep_s TEXT[]
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        -- The caps are the code's own (recaps.store MAX_TRIES and MAX_CORRECTIONS, recaps.style
        -- MAX_NOTES; a test keeps them equal): a caller of this door can't choose them.
        max_tries CONSTANT INT := 2;
        max_corrections CONSTANT INT := 10;
        max_notes CONSTANT INT := 10;
        r recap_requests%ROWTYPE;
        job recap_jobs%ROWTYPE;
        have TEXT[];
        add_c TEXT[] := '{}';
        add_s TEXT[] := '{}';
        item TEXT;
        pick JSONB;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        top INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- The campaign row first, the same order as the Discord notes path, so the two can't
        -- wait on each other.
        PERFORM 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c FOR UPDATE;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.user_id = dmbot_current_user() AND x.state = 'done' FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        FOREACH item IN ARRAY coalesce(keep_c, '{}') LOOP
            SELECT p->>'text' INTO item FROM jsonb_array_elements(r.answer->'corrections') p
                WHERE p->>'id' = item;
            IF item IS NULL THEN
                RETURN jsonb_build_object('status', 'bad_choice');
            END IF;
            add_c := add_c || item;
        END LOOP;
        FOREACH item IN ARRAY coalesce(keep_s, '{}') LOOP
            SELECT p->>'text' INTO item FROM jsonb_array_elements(r.answer->'style') p
                WHERE p->>'id' = item;
            IF item IS NULL THEN
                RETURN jsonb_build_object('status', 'bad_choice');
            END IF;
            add_s := add_s || item;
        END LOOP;
        IF cardinality(add_c) + cardinality(add_s) = 0 THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.status <> 'draft' OR job.state NOT IN ('ready', 'failed') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        IF job.tries >= max_tries THEN
            RETURN jsonb_build_object('status', 'no_tries');
        END IF;
        have := job.corrections;
        FOREACH item IN ARRAY add_c LOOP
            IF NOT item = ANY (have) THEN
                have := have || item;
            END IF;
        END LOOP;
        IF cardinality(have) > max_corrections THEN
            have := have[cardinality(have) - max_corrections + 1 : cardinality(have)];
        END IF;
        FOREACH item IN ARRAY add_s LOOP
            item := regexp_replace(btrim(left(item, 300)), '\s+', ' ', 'g');  -- as NoteStore.add
            DELETE FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND lower(n.text) = lower(item);
            DELETE FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND n.id IN (
                    SELECT o.id FROM recap_style_notes o
                    WHERE o.guild_id = g AND o.campaign_id = c
                    ORDER BY o.position, o.id
                    LIMIT greatest(0, (SELECT count(*) FROM recap_style_notes q
                                       WHERE q.guild_id = g AND q.campaign_id = c)
                                      - max_notes + 1));
            SELECT coalesce(max(position), 0) + 1 INTO top FROM recap_style_notes n
                WHERE n.guild_id = g AND n.campaign_id = c;
            INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,
                                           created_at)
                VALUES (g, c, top, item, dmbot_current_user(), now_s);
        END LOOP;
        UPDATE recap_jobs SET corrections = have, tries = tries + 1, state = 'queued',
            approved_by = 0, approved_at = 0, approved_hash = '', audio_choice = ''
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        DELETE FROM recap_requests WHERE guild_id = g AND id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_rewrite(BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_rewrite(
        BIGINT, TEXT, BIGINT, TEXT, TEXT[], TEXT[]) FROM PUBLIC;
    """
)
SAID_AS_SUGGESTIONS = f"""
    -- "Say it this way?" (#2171): when a confirmed name is again and again heard as the same
    -- other spelling, DMbot offers that spelling for the recap narrator's "said as". One row per
    -- name, so the DM is asked once: a "no" stays as state 'no' and is never offered again.
    -- Words only: no speaker, no line, no time of day. It goes with the name and with the
    -- campaign, is not part of backups (a wait, not content), and is isolated per server.
    CREATE TABLE said_as_suggestions (
        {_memory_scope()}
        entity_id  TEXT NOT NULL,
        heard      TEXT NOT NULL CHECK (length(heard) BETWEEN 1 AND 80),
        times      INTEGER NOT NULL CHECK (times > 0),
        state      TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'no')),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        {_entity_link("entity_id")}
    );
    """ + _isolate("said_as_suggestions")

NAMES_LATER = f"""
    -- A suggested name the DM pressed "Later" on (#2174): the question stays in 📝 Check new names,
    -- but it no longer holds a recap back, so one name the DM cannot decide on does not keep
    -- every recap waiting. One row per name, deleted with the name and with the campaign, left
    -- out of backups (a wait, not content: a restored campaign's suggestions ask again), isolated
    -- per server. A name that is decided on loses its row (`Changes.update`). No speaker, no line,
    -- no words.
    CREATE TABLE name_later (
        {_memory_scope()}
        entity_id   TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        {_entity_link("entity_id")}
    );
    """ + _isolate("name_later")

BACKSTORY_LINKS = f"""
    -- The connections a backstory reading found between names (#2380; docs/PLAN.md, "Names at
    -- scale"), waiting for the DM to keep or refuse each. The same lifetime as the backstory's
    -- name suggestions: until the DM answers, the campaign is deleted, or an end is refused. Two
    -- entries, the relationship's key, a short quote of the DM's own text as evidence (never the
    -- text) and whether the text wrote it with a secret spelling. Deleted with either entry and
    -- with the campaign, left out of backups (a wait, not content), isolated per server.
    CREATE TABLE backstory_links (
        {_memory_scope()}
        subject_id TEXT NOT NULL,
        predicate  TEXT NOT NULL CHECK (char_length(predicate) BETWEEN 1 AND 80),
        object_id  TEXT NOT NULL,
        quote      TEXT NOT NULL CHECK (char_length(quote) <= 400),
        secret     BOOLEAN NOT NULL DEFAULT false,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, subject_id, predicate, object_id),
        CHECK (subject_id <> object_id),
        {_entity_link("subject_id")},
        {_entity_link("object_id")}
    );
    """ + _isolate("backstory_links")

NAME_ANSWERS = (
    f"""
    -- The names a recap kept that DMbot did not know (#2027) and that the DM is asked to check, now
    -- answerable on the recap page (#2113). `recap_name_checks` only says which waiting
    -- suggestions belong to which recap (ids, no words): written when the bot queues them. A
    -- name stops being listed when it is no longer a suggestion, whoever answered it, here or in
    -- Discord's 📝 Check new names. Per campaign, isolated, gone with the name and the campaign,
    -- left out of backups (a pointer, not content).
    CREATE TABLE recap_name_checks (
        {_memory_scope()}
        session_at  BIGINT NOT NULL,
        entity_id   TEXT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_at, entity_id),
        {_entity_link("entity_id")}
    );

    -- An answer the DM gave on the page, waiting for the bot to apply it through the same
    -- MemoryStore paths as Discord's (so the change log, Undo and the lists all agree). The
    -- website has no right on the memory tables, so it only queues the answer; the bot applies
    -- it and marks the row. `text` is the new spelling (at most 80 letters) or the DM's note (at
    -- most 300); a row lives an hour at most and is never logged. Gone with its name and its
    -- campaign, left out of backups, isolated per server.
    CREATE TABLE name_answers (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id   TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL
            CHECK (action IN ('confirm', 'same', 'respell', 'not_a_name', 'explain')),
        kind        TEXT NOT NULL DEFAULT '' CHECK (char_length(kind) <= 40),
        target_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(target_id) <= 32),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= 300),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        {_entity_link("entity_id")}
    );
    -- One answer in flight for a name: a second is "busy" until the first is done.
    CREATE UNIQUE INDEX name_answers_in_flight ON name_answers (guild_id, campaign_id, entity_id)
        WHERE state IN ('waiting', 'working');
    CREATE INDEX name_answers_open ON name_answers (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("recap_name_checks")
    + _isolate("name_answers")
    + r"""
    -- The recap's names to check, for a DM of the campaign: each waiting suggestion the recap
    -- kept (id, name, the note kept with it, whether an answer is on its way or failed), and the
    -- known names a suggestion can be said to be (confirmed, public, not a person at the table).
    -- Never a secret name: a suggestion with a secret spelling is left out, and so is a known
    -- name with any secret spelling (the same rule as the queue door and the bot). NULL for anyone
    -- else.
    CREATE FUNCTION dmbot_recap_names_read(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'name', e.name, 'note', e.description,
                           'answering', EXISTS (
                               SELECT 1 FROM name_answers a
                               WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                                 AND a.state IN ('waiting', 'working')),
                           'error', COALESCE((
                               SELECT a.error FROM name_answers a
                               WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                                 AND a.state = 'failed'
                               ORDER BY a.created_at DESC LIMIT 1), ''))
                       ORDER BY lower(e.name), e.id)
                FROM recap_name_checks k
                JOIN memory_entities e
                  ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                 AND e.id = k.entity_id
                WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
                  AND e.status = 'proposed'
                  AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                  WHERE s.guild_id = g AND s.campaign_id = c
                                    AND s.entity_id = e.id AND s.secret)), '[]'::jsonb),
            'known', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', n.id, 'name', n.name, 'kind', n.type)
                                 ORDER BY lower(n.name), n.id)
                FROM (
                    SELECT e.id, e.name, e.type
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c
                      AND e.status = 'confirmed' AND e.type <> 'person'
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = e.id AND NOT a.secret
                                    AND a.status = 'confirmed'
                                    AND lower(a.text) = lower(e.name))
                      AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                      WHERE s.guild_id = g AND s.campaign_id = c
                                        AND s.entity_id = e.id AND s.secret)
                    ORDER BY lower(e.name), e.id
                    LIMIT 200
                ) n), '[]'::jsonb));
    END
    $fn$;

    -- Queue the DM's answer to one of the recap's names to check. The door checks what it can
    -- see: the caller is a DM of the campaign, the name is one of this recap's waiting
    -- suggestions, the words are within their limits, and nothing is in flight for it; the bot
    -- checks the rest (the kind, the known name) when it applies the answer. Answers 'ok' with
    -- the answer's id, or 'bad_action', 'bad_text', 'bad_target', 'not_found', 'busy' or
    -- 'too_soon'.
    CREATE FUNCTION dmbot_name_answer_add(
        g BIGINT, c TEXT, at_ BIGINT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('confirm', 'same', 'respell', 'not_a_name', 'explain') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]'
           OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)
           OR (act = 'explain' AND char_length(clean) NOT BETWEEN 1 AND 300)
           OR (act IN ('confirm', 'same', 'not_a_name') AND clean <> '')
           OR (act = 'confirm' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'confirm' AND COALESCE(kd, '') <> '')
           OR (act = 'same' AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act <> 'same' AND COALESCE(tgt, '') <> '') THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        -- A merge target must be one the list offers: confirmed, not a person at the table, and
        -- with no secret spelling. (The bot checks again when it applies the answer.)
        IF act = 'same' AND NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person'
                 AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                 WHERE s.guild_id = g AND s.campaign_id = c
                                   AND s.entity_id = t.id AND s.secret)) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_answer:' || g::TEXT || c, 0));
        DELETE FROM name_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF NOT EXISTS (SELECT 1 FROM recap_name_checks k
                       JOIN memory_entities e
                         ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                        AND e.id = k.entity_id
                       WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
                         AND k.entity_id = ent AND e.status = 'proposed'
                         AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                         WHERE s.guild_id = g AND s.campaign_id = c
                                           AND s.entity_id = e.id AND s.secret)) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF EXISTS (SELECT 1 FROM name_answers a
                   WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent
                     AND a.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM name_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        DELETE FROM name_answers a  -- a failed answer is replaced by the new one
            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent
              AND a.state = 'failed';
        INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, created_at)
            VALUES (g, c, rid, ent, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_add('
            'BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_name_answer_add(
        BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    """
)
RECAP_SPLIT = """
    -- How a recap's session was split (#2253): its opening into talk, catch-up and last time's
    -- recap, then play. NULL until the split stage has run (or for a job from before it). Ranges
    -- of line numbers and a flag only, never a word of the transcript, so nothing here needs
    -- consent or row-level changes: the column is on a table that already isolates by server. A
    -- new nullable column changes no rows. The next session reads it to leave this session's
    -- retelling out of what it compares with.
    ALTER TABLE recap_jobs ADD COLUMN split JSONB;
    """

RECAP_CRITICS = """
    -- Where a recap's critics loop is (#2255): the readings done, what the critics last asked for,
    -- the best draft so far and, when it stopped without agreement, the notes left for the DM's
    -- recap page. JSON, NULL until the loop begins. Each step is saved, so a restart never repays
    -- for one that finished. It holds the draft text and the critics' notes about it, which are
    -- the recap's own words on a table that already isolates by server and is deleted with the
    -- campaign; none of it is logged. A new nullable column changes no rows.
    ALTER TABLE recap_jobs ADD COLUMN critics JSONB;
    """

NPC_NOTES = (
    f"""
    -- Every NPC opens its own page, with private notes (#2265; docs/PLAN.md, "Every NPC opens its
    -- own page"). `npc_roster` holds what the NPC list shows the players, per entry, written by
    -- the same pass that keeps the Discord list (`npcs_channel.sync_campaign`, from
    -- `RecallService.roster`), so the page and the list say the same and the website, which has no
    -- right on the memory tables, never needs a second copy of the rules. `view` holds names and
    -- groups only (never a secret name, a clue, a condition, a thread or the DM's note). Gone with
    -- its entry's listing and its campaign; left out of backups (derived, and a backup is
    -- downloadable by anyone in the server).
    CREATE TABLE npc_roster (
        {_memory_scope()}
        entry_id    TEXT NOT NULL,
        view        JSONB NOT NULL,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entry_id)
    );

    -- A person's private note on an NPC: one row for each person and entry, at most 5,000
    -- characters. Only that person ever reads it (the doors below answer the caller's own rows;
    -- not the DM, not another player). Never in a transcript, an AI prompt, the story memory, a log
    -- or a backup. `name` is the entry's name as last listed, so a note whose NPC has gone can
    -- still be shown. `orphaned_at` is NULL while the NPC is listed; when it is merged away the
    -- note moves to the one kept (`MemoryStore.merge`), and when it is rejected or deleted the
    -- note stays 30 days in "Notes on names no longer listed" and then goes (the bot's sweep).
    -- Gone with the campaign.
    CREATE TABLE npc_notes (
        {_memory_scope()}
        entry_id    TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        name        TEXT NOT NULL DEFAULT '' CHECK (char_length(name) <= 200),
        body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 5000),
        updated_at  BIGINT NOT NULL,
        orphaned_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, entry_id, user_id)
    );
    CREATE INDEX npc_notes_person ON npc_notes (guild_id, campaign_id, user_id);
    CREATE INDEX npc_notes_orphans ON npc_notes (orphaned_at) WHERE orphaned_at IS NOT NULL;
    """
    + _isolate("npc_roster")
    + _isolate("npc_notes")
    + r"""
    -- One NPC's page for a member of the server (the session's own server list, like the
    -- transcripts): the listed view, the posted recaps that mention the name or another name it is
    -- listed under (a recap the table has already seen), the caller's own note, and, for a DM of
    -- the campaign, the DM's note on the entry. NULL for anyone not in the server; 'not_found'
    -- for an entry that is not listed (a secret, unconfirmed or another campaign's never is).
    CREATE FUNCTION dmbot_web_npc_page(g BIGINT, c TEXT, e TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        shown JSONB;
        is_dm BOOLEAN;
        names TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT r.view INTO shown FROM npc_roster r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = e;
        IF shown IS NULL THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        names := ARRAY[lower(shown ->> 'name')] || COALESCE(ARRAY(
            SELECT lower(x) FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(shown -> 'alsoCalled') = 'array'
                     THEN shown -> 'alsoCalled' ELSE '[]'::jsonb END) AS x), ARRAY[]::TEXT[]);
        RETURN jsonb_build_object(
            'status', 'ok',
            'view', shown,
            'isDm', is_dm,
            'recaps', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'sessionAt', j.session_at, 'title', j.title, 'day', j.game_day)
                           ORDER BY j.session_at DESC)
                FROM (
                    SELECT rj.session_at, rj.title, rj.game_day FROM recap_jobs rj
                    WHERE rj.guild_id = g AND rj.campaign_id = c AND rj.status = 'posted'
                      AND EXISTS (SELECT 1 FROM unnest(names) AS n
                                  WHERE char_length(n) >= 3
                                    AND position(n IN lower(rj.summary)) > 0)
                    ORDER BY rj.session_at DESC LIMIT 20) j), '[]'::jsonb),
            'myNote', (SELECT jsonb_build_object('body', n.body, 'updatedAt', n.updated_at)
                       FROM npc_notes n
                       WHERE n.guild_id = g AND n.campaign_id = c AND n.entry_id = e
                         AND n.user_id = dmbot_current_user()),
            'dmNote', CASE WHEN is_dm THEN (
                SELECT NULLIF(m.description, '') FROM memory_entities m
                WHERE m.guild_id = g AND m.campaign_id = c AND m.id = e
                  AND m.status = 'confirmed') END);
    END
    $fn$;

    -- Save or clear the caller's own note on a listed NPC. 'ok', 'bad_text' (over 5,000 characters
    -- or a control character), or 'not_found' (not listed); NULL for anyone not in the server. An
    -- empty note clears it.
    CREATE FUNCTION dmbot_web_npc_note_put(g BIGINT, c TEXT, e TEXT, txt TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        shown JSONB;
        clean TEXT := replace(COALESCE(txt, ''), E'\r\n', E'\n');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT r.view INTO shown FROM npc_roster r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = e;
        IF shown IS NULL THEN
            RETURN 'not_found';
        END IF;
        IF clean ~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]' OR char_length(clean) > 5000 THEN
            RETURN 'bad_text';
        END IF;
        IF btrim(clean) = '' THEN
            DELETE FROM npc_notes n
                WHERE n.guild_id = g AND n.campaign_id = c AND n.entry_id = e
                  AND n.user_id = dmbot_current_user();
            RETURN 'ok';
        END IF;
        INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body, updated_at)
            VALUES (g, c, e, dmbot_current_user(), left(shown ->> 'name', 200), clean, now_s)
            ON CONFLICT (guild_id, campaign_id, entry_id, user_id) DO UPDATE
                SET body = EXCLUDED.body, name = EXCLUDED.name, updated_at = EXCLUDED.updated_at,
                    orphaned_at = NULL;
        RETURN 'ok';
    END
    $fn$;

    -- All of the caller's own notes in one campaign, for the download: the NPC's name as last
    -- listed, the note, when it was written, and whether the NPC is still listed. NULL for anyone
    -- not in the server.
    CREATE FUNCTION dmbot_web_npc_notes_mine(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', n.entry_id, 'name', n.name, 'body', n.body,
                       'updatedAt', n.updated_at, 'listed', n.orphaned_at IS NULL)
                       ORDER BY lower(n.name), n.entry_id)
            FROM npc_notes n
            WHERE n.guild_id = g AND n.campaign_id = c AND n.user_id = dmbot_current_user()),
            '[]'::jsonb);
    END
    $fn$;

    -- Delete all of the caller's own notes in one campaign. The number deleted; NULL for anyone
    -- not in the server.
    CREATE FUNCTION dmbot_web_npc_notes_delete(g BIGINT, c TEXT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        gone BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        DELETE FROM npc_notes n
            WHERE n.guild_id = g AND n.campaign_id = c AND n.user_id = dmbot_current_user();
        GET DIAGNOSTICS gone = ROW_COUNT;
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_page(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_notes_mine(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_notes_delete(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_npc_page(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_note_put(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_notes_mine(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_npc_notes_delete(BIGINT, TEXT) FROM PUBLIC;
    """
)

CAMPAIGN_CARD = """
    -- The campaign's own page on the website (#2416) needs the campaign's name for any member of
    -- the server, a player included, and nothing else of it: not its DMs, its settings or one
    -- line of its story. NULL when the caller is not in the server (the session's own list, the
    -- same check as the other web doors) or the campaign is not there, so another server's
    -- campaign looks like none at all.
    CREATE FUNCTION dmbot_web_campaign_card(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found_name TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT k.name INTO found_name FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('name', found_name);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_card(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_card(BIGINT, TEXT) FROM PUBLIC;
"""

RECAP_VOICE = r"""
    -- Speaking instead of typing on the recap page (#2113). The clip waits in the request row
    -- until the bot has written it down; the same statement that stores the text sets the clip
    -- to NULL, and a sweep clears any clip older than ten minutes whatever happened. It is never
    -- written to disk, never logged, and the table is left out of backups. The words are the
    -- DM's own and live in the row for an hour at most.
    ALTER TABLE recap_requests DROP CONSTRAINT recap_requests_kind_check;
    ALTER TABLE recap_requests ADD CONSTRAINT recap_requests_kind_check
        CHECK (kind IN ('instructions', 'voice'));
    ALTER TABLE recap_requests DROP CONSTRAINT recap_requests_body_check;
    ALTER TABLE recap_requests ADD CONSTRAINT recap_requests_body_check CHECK (
        (kind = 'instructions' AND char_length(body) BETWEEN 1 AND 1000)
        OR (kind = 'voice' AND body = ''));
    ALTER TABLE recap_requests ADD COLUMN clip BYTEA
        CHECK (clip IS NULL OR octet_length(clip) BETWEEN 1 AND 2097152);
    ALTER TABLE recap_requests ADD COLUMN clip_type TEXT NOT NULL DEFAULT ''
        CHECK (clip_type IN ('', 'audio/webm', 'audio/ogg'));

    CREATE FUNCTION dmbot_recap_voice_add(
        g BIGINT, c TEXT, at_ BIGINT, data BYTEA, ctype TEXT, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- Best effort (#2163: nothing writes installs.left_at yet): a server the bot has left
        -- takes no new clips. The promise that no clip outlives ten minutes does not rest on this:
        -- the all-server sweep (dmbot_sweep_old_clips) keeps it.
        IF EXISTS (SELECT 1 FROM installs i WHERE i.guild_id = g AND i.left_at IS NOT NULL) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        -- Audio DMbot holds, even for minutes, needs the speaker's agreement to be recorded
        -- under the current wording, naming the outside company in use if there is one
        -- (`terms` and `outside` are the code's TERMS_VERSION and TRANSCRIBER). The bot checks
        -- again.
        IF NOT EXISTS (SELECT 1 FROM consent k
                       WHERE k.guild_id = g AND k.user_id = dmbot_current_user()
                         AND k.terms_version = terms
                         AND (outside IS NULL OR k.outside_to = outside)) THEN
            RETURN jsonb_build_object('status', 'no_consent');
        END IF;
        IF ctype IS NULL OR ctype NOT IN ('audio/webm', 'audio/ogg') THEN
            RETURN jsonb_build_object('status', 'bad_type');
        END IF;
        IF data IS NULL OR octet_length(data) = 0 THEN
            RETURN jsonb_build_object('status', 'bad_clip');
        END IF;
        IF octet_length(data) > 2097152 THEN
            RETURN jsonb_build_object('status', 'too_big');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- A clip older than ten minutes and any row older than an hour go here, for this
        -- campaign, whenever a new clip comes (the bot's own sweep covers the servers it is in).
        UPDATE recap_requests r SET clip = NULL
            WHERE r.guild_id = g AND r.campaign_id = c AND r.clip IS NOT NULL
              AND r.created_at < now_s - 600;
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'voice'
              AND r.state IN ('waiting', 'working', 'done')  -- not a failed one
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    clip, clip_type, created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'voice', '', data, ctype, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Emptying old clips in every server, the ones the bot has left included, by one function
    -- and nothing else: it sets the cleanup marker for its own statement only and does one
    -- thing, empty the clip of a request older than ten minutes. It is the bot's: not granted
    -- to the website's role (the policies below mean nothing without the marker, and the
    -- website's role has no right to the table anyway).
    -- (An UPDATE's new row must pass the SELECT rule too, so that rule cannot ask for the clip.)
    CREATE POLICY old_clips_seen ON recap_requests FOR SELECT
        USING (dmbot_cleanup() = 'old-clips' AND created_at < dmbot_now() - 600);
    CREATE POLICY old_clips_emptied ON recap_requests FOR UPDATE
        USING (dmbot_cleanup() = 'old-clips' AND clip IS NOT NULL
               AND created_at < dmbot_now() - 600)
        WITH CHECK (dmbot_cleanup() = 'old-clips' AND clip IS NULL);
    CREATE FUNCTION dmbot_sweep_old_clips() RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        emptied BIGINT;
    BEGIN
        PERFORM set_config('dmbot.cleanup', 'old-clips', true);
        UPDATE recap_requests SET clip = NULL
            WHERE clip IS NOT NULL AND created_at < dmbot_now() - 600;
        GET DIAGNOSTICS emptied = ROW_COUNT;
        PERFORM set_config('dmbot.cleanup', '', true);
        RETURN emptied;
    END
    $fn$;

    -- A clip's state and the words written from it, to the DM who sent it and no one else.
    CREATE FUNCTION dmbot_recap_voice_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.kind = 'voice' AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer);
    END
    $fn$;
    -- The typed notes' limit counts typed notes only: clips have their own (above).
    CREATE OR REPLACE FUNCTION dmbot_recap_request_add(g BIGINT, c TEXT, at_ BIGINT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recap_jobs j
                       WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                         AND j.state = 'ready' AND j.status = 'draft') THEN
            RETURN jsonb_build_object('status', 'not_editable');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_request:' || g::TEXT || c, 0));
        -- The one-hour promise holds for a server the bot has left too: old words go here.
        DELETE FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 3600;
        IF (SELECT count(*) FROM recap_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'instructions'
              AND r.state IN ('waiting', 'working')
              AND r.created_at > now_s - 600) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind, body,
                                    created_at)
            VALUES (g, c, rid, at_, dmbot_current_user(), 'instructions', btrim(txt), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_add(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());  -- (reset by REPLACE)
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_voice_add(BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_sweep_old_clips()'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_voice_add(
        BIGINT, TEXT, BIGINT, BYTEA, TEXT, INT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_sweep_old_clips() FROM PUBLIC;
    """

INSTALLS_LEFT_EXCEPT = r"""
    -- Servers DMbot was removed from while it was offline (#2166): at start-up the bot lists the
    -- servers it is in, and every install row that still says it is there but is not in the list
    -- gets `left_at`. A plain UPDATE can't do it: the bot's connection sees one server's rows at
    -- a time. This door is the exception; its policy needs the setting the function sets, only
    -- opens the update of a row that has not left, and is TO the role that owns the function
    -- (never the website's, whose own restrictive policy on installs would refuse it anyway).
    -- The function is for the bot: no one else is granted it. It changes no one's installer,
    -- reads nothing back, and returns a count.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY install_left_sweep ON installs FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''installs-left'' AND left_at IS NULL)
                WITH CHECK (dmbot_owner_sync() = ''installs-left'' AND left_at IS NOT NULL)',
            current_user);
        EXECUTE format(
            'CREATE POLICY install_left_sweep_read ON installs FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''installs-left'')',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_installs_left_except(present BIGINT[]) RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n INTEGER;
    BEGIN
        -- An empty or missing list means the caller could not see its servers, not that it is in
        -- none: marking everything left would be wrong, so nothing is marked.
        IF present IS NULL OR cardinality(present) = 0 THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'installs-left', true);
        UPDATE installs
            SET left_at = floor(extract(epoch FROM clock_timestamp()))::BIGINT
            WHERE left_at IS NULL AND NOT (guild_id = ANY (present));
        GET DIAGNOSTICS n = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN n;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_installs_left_except(BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_installs_left_except(BIGINT[]) FROM PUBLIC;
"""

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
ALL_NAMES_PAGE = r"""
    -- The website's All names page (#2294): one page of a campaign's confirmed names, searched and
    -- filtered, with the count of each kind. Answers a DM of the campaign (everything) and a server
    -- manager who is not one (the same names without what only a DM sees: the entries the players
    -- don't know yet, a clue only some know, the story threads and deeds, and any name whose own
    -- spelling is secret, and no secret spellings). NULL for anyone else, so the page says "not
    -- found" exactly as for a missing campaign. The website's role has no grant on the memory
    -- tables; this door is its only way to read them.
    CREATE FUNCTION dmbot_web_all_names(
        g BIGINT, c TEXT, q TEXT, k TEXT, newest BOOLEAN, pg INT, size INT, only_id TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        cname TEXT;
        needle TEXT := NULLIF(lower(btrim(COALESCE(q, ''))), '');
        kind_ TEXT := NULLIF(k, '');
        result JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF NOT is_dm AND NOT (g = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT name INTO cname FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        WITH base AS (
            SELECT e.id, e.name, e.created_at,
                   CASE WHEN e.type = 'character' THEN
                            CASE e.role WHEN 'player_character' THEN 'player_character'
                                        WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                        ELSE 'character' END
                        ELSE e.type END AS kind,
                   EXISTS (SELECT 1 FROM memory_aliases a
                           WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                             AND a.status <> 'rejected' AND a.secret
                             AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                               = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                   ) AS secret,
                   (e.audience_limited OR e.hidden_from_players) AS hidden
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c
              AND e.status = 'confirmed' AND e.type <> 'person'
              AND (is_dm OR (NOT e.audience_limited AND NOT e.hidden_from_players
                             AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                'promise', 'deed')))
        ), vis AS (
            SELECT * FROM base WHERE is_dm OR NOT secret
        ), hit AS (
            SELECT v.* FROM vis v
            WHERE (kind_ IS NULL OR v.kind = kind_)
              AND (only_id IS NULL OR v.id = only_id)
              AND (needle IS NULL
                   OR position(needle IN lower(v.name)) > 0
                   OR EXISTS (SELECT 1 FROM memory_aliases a
                              WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = v.id
                                AND a.status = 'confirmed' AND (is_dm OR NOT a.secret)
                                AND position(needle IN lower(a.text)) > 0))
        ), pageful AS (
            SELECT h.* FROM hit h
            ORDER BY CASE WHEN newest THEN NULL ELSE lower(h.name) END,
                     CASE WHEN newest THEN h.created_at END DESC, h.id
            LIMIT GREATEST(size, 1) OFFSET GREATEST(pg - 1, 0)::BIGINT * GREATEST(size, 1)
        )
        SELECT jsonb_build_object(
            'campaign', cname,
            'canEdit', is_dm,
            'total', (SELECT count(*) FROM hit),
            'kinds', COALESCE((SELECT jsonb_object_agg(kind, n)
                               FROM (SELECT kind, count(*) AS n FROM vis GROUP BY kind) t),
                              '{}'::jsonb),
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', p.id, 'name', p.name, 'kind', p.kind, 'secret', p.secret,
                    'hidden', p.hidden,
                    'others', COALESCE((
                        SELECT jsonb_agg(a.text ORDER BY lower(a.text), a.id)
                        FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = p.id
                          AND a.status = 'confirmed' AND NOT a.secret
                          AND lower(a.text) <> lower(p.name)), '[]'::jsonb),
                    'secretOthers', CASE WHEN is_dm THEN COALESCE((
                        SELECT jsonb_agg(a.text ORDER BY lower(a.text), a.id)
                        FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = p.id
                          AND a.status = 'confirmed' AND a.secret
                          AND lower(a.text) <> lower(p.name)), '[]'::jsonb)
                        ELSE '[]'::jsonb END,
                    'npcId', CASE WHEN p.kind = 'npc' AND EXISTS (
                        SELECT 1 FROM npc_roster r
                        WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = p.id)
                        THEN p.id END)
                    ORDER BY CASE WHEN newest THEN NULL ELSE lower(p.name) END,
                             CASE WHEN newest THEN p.created_at END DESC, p.id)
                FROM pageful p), '[]'::jsonb))
        INTO result;
        RETURN result;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)
        FROM PUBLIC;
"""
# The All names door again (#2376, migration 0174): the check for an entry whose own spelling is
# also a secret name ran once per entry over all of its names, which took seconds on a big campaign
# before the database had statistics (right after a restore or an import). It is now found from the
# secret names alone and joined once. The result is the same. 0170 is already applied everywhere,
# so this redefines the door; a redefined function loses its pinned settings, so they are set again.
_SECRET_EACH = """                   EXISTS (SELECT 1 FROM memory_aliases a
                           WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                             AND a.status <> 'rejected' AND a.secret
                             AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                               = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                   ) AS secret,"""
_SECRET_ONCE = """                   (e.id IN (SELECT s.entity_id FROM secret_own s)) AS secret,"""
_SECRET_CTE = """        WITH secret_own AS (
            SELECT DISTINCT a.entity_id
            FROM memory_aliases a
            JOIN memory_entities en ON en.guild_id = a.guild_id AND en.campaign_id = a.campaign_id
                                   AND en.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND a.secret AND a.status <> 'rejected'
              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
        ), base AS ("""
assert _SECRET_EACH in ALL_NAMES_PAGE and ALL_NAMES_PAGE.count("        WITH base AS (") == 1
ALL_NAMES_SPEED = (
    ALL_NAMES_PAGE.replace(
        "CREATE FUNCTION dmbot_web_all_names(", "CREATE OR REPLACE FUNCTION dmbot_web_all_names(", 1
    )
    .replace(_SECRET_EACH, _SECRET_ONCE, 1)
    .replace("        WITH base AS (", _SECRET_CTE, 1)
)
# Which suggested names were made before pronouns were kept out of them (#2358). The default
# marks every existing row (a constant default is stored as metadata: no UPDATE runs, so no
# row-level-security policy is opened) and is then changed, so every row written from now on is
# false with no insert path changed. The recap page's door tells the website which are old.
_NAMES_READ = NAME_ANSWERS[
    NAME_ANSWERS.index("    CREATE FUNCTION dmbot_recap_names_read") : NAME_ANSWERS.index(
        "    -- Queue the DM's answer"
    )
]
NAME_GATE = (
    """
    ALTER TABLE memory_entities
        ADD COLUMN made_before_name_gate BOOLEAN NOT NULL DEFAULT true;
    ALTER TABLE memory_entities ALTER COLUMN made_before_name_gate SET DEFAULT false;
    """
    + _NAMES_READ.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
        "'id', e.id, 'name', e.name,",
        # the same rule as `hidden_old_pronoun`: never the DM's own entry
        "'id', e.id, 'name', e.name,"
        " 'old', (e.made_before_name_gate AND e.source IS DISTINCT FROM 'dm'),",
    )
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """
)


HOUSE_FILE_READ = """
    -- The linked house-rules file read by the AI (#2377): a hash of the file's text as it was
    -- when last read (spacing and case ignored), so the AI is asked again only when the file
    -- changes. Never the text. A new link forgets it. Added with no default, so no row changes.
    ALTER TABLE house_rules_file ADD COLUMN read_fingerprint TEXT
        CHECK (read_fingerprint IS NULL OR read_fingerprint ~ '^[0-9a-f]{64}$');
"""

NAME_ACTIONS = (
    f"""
    -- What a DM did on the website's All names page (#2294), waiting for the bot. The website has
    -- no right on the memory tables, so its door only checks the signed-in DM and queues the
    -- action here; the bot applies it through the same MemoryStore paths as Discord's name card
    -- (so the change log, Undo and every list agree), re-checks that the sender is still a DM of
    -- the campaign and that the name is still there, and marks the row. Applied in the order made
    -- (`seq`), so a "spell" then a "merge" never run backwards. `text` is a spelling or another
    -- name (at most 100), `flag` the secret switch or the remove confirmation. `result_id` is the
    -- name whose row the page shows afterwards (the survivor of a merge). `secret` marks or unmarks
    -- one other name (`text`); `hide` is the whole entry's "players don't know yet" (`flag`). A row
    -- lives an hour at most and its words are never logged. Gone with its campaign, left out of
    -- backups, isolated per server, and read back only by the DM who made it.
    CREATE TABLE name_actions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        entity_id   TEXT NOT NULL CHECK (char_length(entity_id) <= 32),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN
            ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove')),
        kind        TEXT NOT NULL DEFAULT '' CHECK (char_length(kind) <= 40),
        target_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(target_id) <= 32),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= 100),
        flag        BOOLEAN NOT NULL DEFAULT false,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        result_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(result_id) <= 32),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX name_actions_open ON name_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("name_actions")
    + r"""
    -- Queue one action. The door checks what it can see: the caller is a DM of the campaign, the
    -- name is a confirmed one of it (not a person at the table), the words are within their
    -- limits, a merge target is another confirmed name, a removal carries its confirmation, and
    -- the campaign's queue is not full. The bot checks the rest when it applies it. Answers
    -- 'ok' with the id, or 'bad_action', 'bad_text', 'bad_target', 'needs_confirm', 'not_found'
    -- or 'too_soon'; NULL for anyone who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_name_action_add(
        g BIGINT, c TEXT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN
               ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove')
        THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]' OR char_length(clean) > 100
           OR (act IN ('spell', 'add-other', 'remove-other', 'secret') AND clean = '')
           OR (act IN ('kind', 'hide', 'merge', 'remove') AND clean <> '')
           OR (act = 'kind' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'kind' AND COALESCE(kd, '') <> '')
           OR (act = 'merge' AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act <> 'merge' AND COALESCE(tgt, '') <> '')
           OR (act IN ('secret', 'hide') AND fl IS NULL) THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF act = 'remove' AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        IF ent IS NULL OR NOT EXISTS (
               SELECT 1 FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ent
                 AND e.status = 'confirmed' AND e.type <> 'person') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF act = 'merge' AND (tgt = ent OR NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:' || g::TEXT || c, 0));
        DELETE FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, flag, created_at)
            VALUES (g, c, rid, ent, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one action, for the DM who made it (nobody else's): 'waiting', 'working',
    -- 'done' or 'failed' with its reason word, and the name to show afterwards. NULL when there is
    -- no such action of the caller's in this campaign.
    CREATE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SETTING_ACTIONS = (
    f"""
    -- What a DM chose on the website's Settings page (#2463), waiting for the bot: who can see the
    -- DM screen, how much DMbot says, how long transcripts are kept. The website has no right on
    -- the campaign tables, so its door only checks the signed-in DM and queues the choice here; the
    -- bot applies it through the same calls as Discord's ⚙️ Settings buttons (so the channel
    -- permissions, the notices and the live session follow), after checking again that the sender
    -- is still a DM of the campaign. Applied in the order made (`seq`). `value` is the choice
    -- (private/peek/open, quiet/normal, forever/365/90/30); `flag` is the DM's yes to a shorter
    -- transcript limit, which deletes old sessions, so it is asked for first, as in Discord. `note`
    -- is what the DM sees afterwards (the same words Discord says). A row lives an hour at most,
    -- its words are never logged, and it is gone with its campaign, left out of backups, isolated
    -- per server and read back only by the DM who made it.
    CREATE TABLE setting_actions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        setting     TEXT NOT NULL CHECK (setting IN ('visibility', 'level', 'retention')),
        value       TEXT NOT NULL CHECK (char_length(value) <= 10),
        flag        BOOLEAN NOT NULL DEFAULT false,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 1000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX setting_actions_open ON setting_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("setting_actions")
    + r"""
    -- Queue one choice. The door checks what it can see: the caller is a DM of the campaign, the
    -- setting and its value are ones Discord offers, and the campaign's queue is not full. The bot
    -- checks the rest when it applies it. Answers 'ok' with the id, or 'bad_setting', 'bad_value'
    -- or 'too_soon'; NULL for anyone who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_setting_add(
        g BIGINT, c TEXT, st TEXT, val TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF st IS NULL OR st NOT IN ('visibility', 'level', 'retention') THEN
            RETURN jsonb_build_object('status', 'bad_setting');
        END IF;
        IF val IS NULL
           OR (st = 'visibility' AND val NOT IN ('private', 'peek', 'open'))
           OR (st = 'level' AND val NOT IN ('quiet', 'normal'))
           OR (st = 'retention' AND val NOT IN ('forever', '365', '90', '30')) THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, flag,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), st, val, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the DM who made it (nobody else's): 'waiting', 'working',
    -- 'done' or 'failed' with its reason word, and the notice to show. NULL when there is no such
    -- choice of the caller's in this campaign.
    CREATE FUNCTION dmbot_web_setting_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r setting_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'setting', r.setting,
                                  'value', r.value, 'note', r.note);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_setting_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
WEB_RELATIONSHIPS = r"""
    -- The relationship web on the website (#2323): the names within N steps of one name, and the
    -- confirmed connections between them, for the page that draws them. Read only; the words for a
    -- connection are made by the API from the predicates, never here.
    --
    -- Who sees what: a DM of the campaign gets everything confirmed, secret connections and
    -- names included (flagged). Any other member of the server gets the players' view: no entry
    -- the players don't know yet or that is limited to some of them, no thread-like kinds, no
    -- name whose own spelling is secret, no secret connection. The same rule as the All names
    -- page. NULL when the caller is not in the server or there is no such campaign;
    -- {status: 'not_found'} for a centre the caller may not see.
    --
    -- `center_`: the name to centre on, or NULL for the most connected one. `hops`: 1 and up.
    -- `cap`: the most names returned; past it `truncated` is true. `size_only`: just how many names
    -- are within the steps (before the cap).
    CREATE FUNCTION dmbot_web_relationships(
        g BIGINT, c TEXT, center_ TEXT, hops INT, cap INT, size_only BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        vis TEXT[];
        rel_ids TEXT[];
        start_id TEXT := center_;
        within TEXT[];
        total INT;
        kept TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        vis := ARRAY(
            WITH secret_own AS (
                SELECT DISTINCT a.entity_id
                FROM memory_aliases a
                JOIN memory_entities en ON en.guild_id = a.guild_id
                                       AND en.campaign_id = a.campaign_id AND en.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c AND a.secret AND a.status <> 'rejected'
                  AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                    = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
            )
            SELECT e.id FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c
              AND e.status = 'confirmed' AND e.type <> 'person'
              AND (is_dm OR (NOT e.audience_limited AND NOT e.hidden_from_players
                             AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                'promise', 'deed')
                             AND e.id NOT IN (SELECT s.entity_id FROM secret_own s))));
        -- The connections the caller may see, worked out once (every use below reads this list).
        -- A member sees what the NPC pages show (memory/recall.py): no secret one, none that has
        -- ended, none that came from the story plan. A DM sees all the confirmed ones.
        rel_ids := ARRAY(
            SELECT r.id FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'
              AND r.predicate <> 'led_to'
              AND r.subject_id = ANY (vis) AND r.object_id = ANY (vis)
              AND (is_dm OR (NOT r.secret AND r.to_session_at IS NULL
                             AND r.source NOT IN ('plan', 'story_plan'))));
        IF start_id IS NULL THEN
            SELECT x.id INTO start_id FROM (
                SELECT e.id, e.name,
                       (SELECT count(*) FROM memory_relations r
                        WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                          AND (r.subject_id = e.id OR r.object_id = e.id)) AS degree
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (vis)) x
            ORDER BY x.degree DESC, lower(x.name), x.id LIMIT 1;
        ELSIF NOT (start_id = ANY (vis)) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF start_id IS NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'center', NULL, 'canEdit', is_dm,
                                      'names', 0, 'nodes', '[]'::jsonb, 'edges', '[]'::jsonb,
                                      'truncated', false);
        END IF;
        WITH RECURSIVE adj AS (
            -- each connection both ways, so the walk joins on one column (no OR)
            SELECT r.subject_id AS a, r.object_id AS b FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
            UNION ALL
            SELECT r.object_id, r.subject_id FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
        ), walk(id, depth) AS (
            SELECT start_id, 0
            UNION
            SELECT adj.b, w.depth + 1
            FROM walk w JOIN adj ON adj.a = w.id
            WHERE w.depth < hops
        )
        SELECT ARRAY(SELECT x.id FROM (SELECT w.id, min(w.depth) AS d FROM walk w GROUP BY w.id) x
                     JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c AND e.id = x.id
                     ORDER BY x.d, lower(e.name), x.id)
        INTO within;
        total := COALESCE(array_length(within, 1), 0);
        IF size_only THEN
            RETURN jsonb_build_object('status', 'ok', 'names', total);
        END IF;
        kept := within[1:GREATEST(cap, 1)];
        RETURN jsonb_build_object(
            'status', 'ok', 'center', start_id, 'canEdit', is_dm, 'names', total,
            'truncated', total > GREATEST(cap, 1),
            'nodes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', e.id, 'name', e.name,
                    'kind', CASE WHEN e.type = 'character' THEN
                                CASE e.role WHEN 'player_character' THEN 'player_character'
                                            WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                            ELSE 'character' END
                            ELSE e.type END,
                    'secret', CASE WHEN is_dm THEN EXISTS (
                        SELECT 1 FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                          AND a.status <> 'rejected' AND a.secret
                          AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                            = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')) END)
                    ORDER BY array_position(kept, e.id))
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (kept)), '[]'::jsonb),
            'edges', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', r.id, 'a', r.subject_id, 'b', r.object_id, 'predicate', r.predicate,
                    'detail', r.detail, 'secret', r.secret, 'at', r.created_at)
                    ORDER BY r.created_at, r.id)
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                  AND r.subject_id = ANY (kept) AND r.object_id = ANY (kept)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)
        FROM PUBLIC;
"""

WEB_LINK_ACTIONS = r"""
    -- Edits to the connections on the relationship web (#2323), through the same queue as the All
    -- names page's actions: the website queues them, the bot applies them through the same
    -- MemoryStore calls as Discord's name card (so the change log and Undo agree) and the page
    -- polls. New actions: 'link-add' (`entity_id` and `target_id` are the two names, `text` the
    -- words, `flag` secret), 'link-words' (`target_id` is the connection, `text` the new words),
    -- 'link-secret' (`target_id`, `flag`) and 'link-remove' (`target_id`, `flag` the confirmation).
    -- `result_other` is the other end of the pair the page shows afterwards (`result_id` is the
    -- first), so it can draw the edge as it now stands. No row changes: a new column with a
    -- constant default, and a wider list of actions.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove',
         'link-add', 'link-words', 'link-secret', 'link-remove'));
    ALTER TABLE name_actions ADD COLUMN result_other TEXT NOT NULL DEFAULT ''
        CHECK (char_length(result_other) <= 32);

    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(
        g BIGINT, c TEXT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
        subject TEXT := ent;
        is_link BOOLEAN := act LIKE 'link-%';
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN
               ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove',
                'link-add', 'link-words', 'link-secret', 'link-remove')
        THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]' OR char_length(clean) > 100
           OR (act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words')
               AND clean = '')
           OR (act IN ('kind', 'hide', 'merge', 'remove', 'link-secret', 'link-remove')
               AND clean <> '')
           OR (act = 'kind' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'kind' AND COALESCE(kd, '') <> '')
           OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove')
               AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove')
               AND COALESCE(tgt, '') <> '')
           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)
           OR (act = 'link-words' AND COALESCE(fl, false)) THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF act IN ('remove', 'link-remove') AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        IF is_link AND act <> 'link-add' THEN
            -- an edit to a connection (words, secret, remove, pin): it must be a confirmed one
            -- of this campaign, between two
            -- confirmed names; the entry the action is filed under is its first name
            SELECT r.subject_id INTO subject FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = tgt AND r.status = 'confirmed'
              AND r.predicate <> 'led_to'
              AND EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = g
                          AND x.campaign_id = c AND x.id = r.subject_id
                          AND x.status = 'confirmed' AND x.type <> 'person')
              AND EXISTS (SELECT 1 FROM memory_entities y WHERE y.guild_id = g
                          AND y.campaign_id = c AND y.id = r.object_id
                          AND y.status = 'confirmed' AND y.type <> 'person');
            IF NOT FOUND THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
        ELSIF subject IS NULL OR NOT EXISTS (
               SELECT 1 FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id = subject
                 AND e.status = 'confirmed' AND e.type <> 'person') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF act IN ('merge', 'link-add') AND (tgt = subject OR NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:' || g::TEXT || c, 0));
        DELETE FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, flag, created_at)
            VALUES (g, c, rid, subject, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id, 'resultOther', r.result_other);
    END
    $fn$;
    -- (redefined functions lose their pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

# Merging several names at once (#2426, migration 0183). Three things:
# 1. the website's All names queue takes `merge-many`: the kept name is the row's entry and `text`
#    holds the other names' ids, space apart (at most 50 of 32 letters), so the column is wider for
#    that one action only;
# 2. the DM's "Not the same" for two names, remembered so the look-alike finder never offers the
#    pair again (the DM's alone; never in backups, recall, transcripts or any player view);
# 3. the All names list says how many times each name was heard (`mentions`), so the page can put
#    the most used first under "Keep which name?".
_NAME_ACTION_DOOR = WEB_LINK_ACTIONS[
    WEB_LINK_ACTIONS.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(") : (
        WEB_LINK_ACTIONS.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read")
    )
]
_MANY = "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'"
_WITH_MANY = (
    "('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',"
    " 'remove',\n         'link-add', 'link-words', 'link-secret', 'link-remove')"
)
assert "'merge', 'remove',\n                'link-add'" in _NAME_ACTION_DOOR
MERGE_MANY = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        """
    + _WITH_MANY
    + r""");
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check
        CHECK (char_length(text) <= CASE WHEN action = 'merge-many' THEN 1700 ELSE 100 END);
    """
    + _NAME_ACTION_DOOR.replace(
        "'merge', 'remove',\n                'link-add'",
        "'merge', 'merge-many', 'remove',\n                'link-add'",
    )
    .replace(
        "char_length(clean) > 100",
        # (in brackets: plpgsql would take the CASE's THEN for the end of the IF's test)
        "char_length(clean) > (CASE WHEN act = 'merge-many' THEN 1700 ELSE 100 END)",
    )
    .replace(
        "           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)",
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ " + _MANY + "))\n"
        "           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)",
        1,
    )
    .replace(
        "        PERFORM pg_advisory_xact_lock(",
        """        IF act = 'merge-many' AND (
               ent = ANY (string_to_array(clean, ' '))
               OR (SELECT count(DISTINCT x) FROM unnest(string_to_array(clean, ' ')) x)
                  <> cardinality(string_to_array(clean, ' '))
               OR (SELECT count(*) FROM memory_entities t
                   WHERE t.guild_id = g AND t.campaign_id = c
                     AND t.id = ANY (string_to_array(clean, ' '))
                     AND t.status = 'confirmed' AND t.type <> 'person')
                  <> cardinality(string_to_array(clean, ' '))) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(""",
        1,
    )
    + f"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- Two names the DM said are not the same: `a_id` sorts before `b_id`, so a pair is one row.
    -- Gone with either name (and its campaign); isolated per server; not in backups.
    CREATE TABLE name_not_same (
        {_memory_scope()}
        a_id        TEXT NOT NULL,
        b_id        TEXT NOT NULL,
        decided_by  BIGINT NOT NULL CHECK (decided_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, a_id, b_id),
        CHECK (a_id < b_id),
        {_entity_link("a_id")},
        {_entity_link("b_id")}
    );
    CREATE INDEX name_not_same_b ON name_not_same (guild_id, campaign_id, b_id);
    """
    + _isolate("name_not_same")
)
_MENTIONS_OF = """'hidden', p.hidden,
                    'mentions', (SELECT count(*) FROM memory_mentions m
                                 WHERE m.guild_id = g AND m.campaign_id = c
                                   AND m.entity_id = p.id),"""
assert ALL_NAMES_SPEED.count("'hidden', p.hidden,") == 1
ALL_NAMES_MENTIONS = ALL_NAMES_SPEED.replace("'hidden', p.hidden,", _MENTIONS_OF, 1)
# The Names page's type filter takes several kinds at once (owner request, 2026-10-27): the
# door's `k` is a comma-separated list of kinds, and a name is kept when its kind is any of them.
# A one-kind list is what it took before, so every caller keeps working. (A redefined function loses
# its pinned settings, so the copy of the door's tail that sets them again comes with it.)
assert ALL_NAMES_MENTIONS.count("(kind_ IS NULL OR v.kind = kind_)") == 1
# The door also bounds the page itself (no website route asks for more than 500, but a door is
# asked by whoever holds the role): `LEAST(GREATEST(size, 1), 500)` wherever the size is used.
assert ALL_NAMES_MENTIONS.count("GREATEST(size, 1)") == 2
ALL_NAMES_KINDS = ALL_NAMES_MENTIONS.replace(
    "(kind_ IS NULL OR v.kind = kind_)",
    "(kind_ IS NULL OR v.kind = ANY (string_to_array(kind_, ',')))",
    1,
).replace("GREATEST(size, 1)", "LEAST(GREATEST(size, 1), 500)")

# Search by meaning on the Names page (#2733, migration 0253).
# `only_id` takes a short comma-separated list of ids as well as one (entry ids are 32 hex digits,
# so a comma never belongs to one): the page asks for the rows of the names the search found, in
# the rows' usual shape, with one call instead of one for each name.
assert ALL_NAMES_KINDS.count("(only_id IS NULL OR v.id = only_id)") == 1
# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
ALL_NAMES_LIST = ALL_NAMES_KINDS.replace(
    "(only_id IS NULL OR v.id = only_id)",
    "(only_id IS NULL OR v.id = ANY (string_to_array(only_id, ',')))",
    1,
)
# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
NAMES_SMART = (
    r"""
    -- What an entry is searched by (#2733), as parts: the name and the description, the other
    -- names it is called (not the secret ones) and its connections, each as the relationship's
    -- key, the campaign's own label for it (NULL for a core relationship, whose words are in
    -- code: `memory/entity_text.py` puts the text together), the other name, which way it runs
    -- and whether it reads the same both ways. A connection the DM keeps secret, or one to an
    -- entry the players don't know yet, is left out, so nothing a player may not know is in the
    -- text of a name they may. NULL when the entry has no description (nothing is searched by a
    -- bare name). The bot's numbers and the Names page's reranker both read it, so they read the
    -- same words. The rules for what a name's text may hold are the players' own (the same as
    -- `meaning.entry_texts` and the website's readers): a connection the DM has not revealed, one
    -- to a story-planning entry, or one to an entry whose own name is a secret never goes in. Text
    -- already made can lag until the next embedding run after an entry becomes hidden.
    CREATE FUNCTION dmbot_entity_parts(g BIGINT, c TEXT, eid TEXT) RETURNS JSONB
    LANGUAGE sql STABLE AS $fn$
        SELECT jsonb_build_object(
            'name', e.name,
            'description', e.description,
            'aliases', COALESCE((
                SELECT jsonb_agg(t.text ORDER BY t.text)
                FROM (SELECT DISTINCT a.text FROM memory_aliases a
                      WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                        AND a.status = 'confirmed' AND NOT a.secret
                        AND lower(a.text) <> lower(e.name)
                      ORDER BY a.text LIMIT 6) t), '[]'::jsonb),
            'links', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'predicate', k.predicate, 'label', k.label, 'symmetric', k.symmetric,
                           'other', k.other, 'outgoing', k.outgoing)
                       ORDER BY k.other, k.predicate)
                FROM (SELECT DISTINCT r.predicate, p.label, p.is_symmetric AS symmetric,
                             o.name AS other, (r.subject_id = e.id) AS outgoing
                      FROM memory_relations r
                      LEFT JOIN memory_predicates p
                        ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                       AND p.key = r.predicate
                      JOIN memory_entities o
                        ON o.guild_id = r.guild_id AND o.campaign_id = r.campaign_id
                       AND o.id = CASE WHEN r.subject_id = e.id THEN r.object_id
                                       ELSE r.subject_id END
                      WHERE r.guild_id = g AND r.campaign_id = c
                        AND r.status = 'confirmed' AND NOT r.secret AND NOT r.unrevealed
                        AND (r.subject_id = e.id OR r.object_id = e.id)
                        AND o.status = 'confirmed' AND o.merged_into IS NULL
                        AND o.type <> 'person' AND NOT o.audience_limited
                        AND NOT o.hidden_from_players
                        AND o.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                           'promise', 'deed')
                        AND NOT EXISTS (
                            SELECT 1 FROM memory_aliases sa
                            WHERE sa.guild_id = o.guild_id AND sa.campaign_id = o.campaign_id
                              AND sa.entity_id = o.id AND sa.secret AND sa.status <> 'rejected'
                              AND regexp_replace(lower(sa.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(o.name), '[^[:alnum:]]', '', 'g'))
                      ORDER BY other, predicate LIMIT 40) k), '[]'::jsonb))
        FROM memory_entities e
        WHERE e.guild_id = g AND e.campaign_id = c AND e.id = eid
          AND char_length(btrim(e.description)) > 0
    $fn$;
    REVOKE ALL ON FUNCTION dmbot_entity_parts(BIGINT, TEXT, TEXT) FROM PUBLIC;

    -- The Names page's search by meaning (#2733): the entries whose kept numbers are closest to the
    -- search's numbers (`q`, made by the website's server from the DM's own words), best first,
    -- with the parts of the text each was made from (`dmbot_entity_parts`) so the reranker can read
    -- it. The comparing is done here because the website's role has no right on the numbers. A DM
    -- of the campaign only (NULL for anyone else, a server manager who is not a DM included: the
    -- text holds descriptions, which a manager does not see on the page). One campaign, one
    -- model, the same number of numbers; the vectors are unit length, but the length is divided
    -- out anyway. At most `pool` (60) rows, from the newest 10,000 numbers (about 3 seconds of one
    -- core for 20,000 rows, measured); `capped` says there were more. An empty `q` only proves who
    -- is asking.
    CREATE FUNCTION dmbot_web_names_near(g BIGINT, c TEXT, m TEXT, q REAL[], pool INT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qn DOUBLE PRECISION;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        qn := sqrt((SELECT sum(x::DOUBLE PRECISION * x) FROM unnest(q) AS x));
        IF qn IS NULL OR qn = 0 OR cardinality(q) > 4096 THEN
            RETURN jsonb_build_object('capped', false, 'rows', '[]'::jsonb);
        END IF;
        RETURN jsonb_build_object('capped', EXISTS (
            SELECT 1 FROM memory_vectors x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.owner_kind = 'assertion'
              AND x.model = m AND cardinality(x.vector) = cardinality(q)
            OFFSET 9999), 'rows', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('id', n.id, 'parts', n.parts, 'score', n.score)
                             ORDER BY n.score DESC, n.id)
            FROM (
                SELECT best.id, dmbot_entity_parts(g, c, best.id) AS parts, best.score
                FROM (
                    SELECT split_part(v.owner_id, '#', 1) AS id,
                           max(d.dot / (qn * d.len)) AS score
                    FROM (SELECT x.owner_id, x.vector FROM memory_vectors x
                          WHERE x.guild_id = g AND x.campaign_id = c
                            AND x.owner_kind = 'assertion' AND x.model = m
                            AND cardinality(x.vector) = cardinality(q)
                          ORDER BY x.made_at DESC, x.owner_id LIMIT 10000) v
                    CROSS JOIN LATERAL (
                        SELECT sum(a::DOUBLE PRECISION * b) AS dot,
                               sqrt(sum(a::DOUBLE PRECISION * a)) AS len
                        FROM unnest(v.vector, q) AS u(a, b)) d
                    WHERE d.len > 0
                    GROUP BY 1
                    ORDER BY 2 DESC, 1
                    LIMIT LEAST(GREATEST(pool, 1), 60)
                ) best
                JOIN memory_entities e
                  ON e.guild_id = g AND e.campaign_id = c AND e.id = best.id
                 AND e.status = 'confirmed' AND e.merged_into IS NULL AND e.type <> 'person'
            ) n
            WHERE n.parts IS NOT NULL), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT) FROM PUBLIC;
    """
    + ALL_NAMES_LIST
)


TRANSCRIPT_SPEAKER_NAMES = """
    -- Each speaker's Discord display name as DMbot kept it (#2337): at the session, or for an older
    -- session when the start-up fill-in found them. The website's transcripts show these instead
    -- of "Speaker 1". A JSON object of Discord user id (text) to the name; "" means they were not
    -- in the server (not found), so DMbot does not look again. Kept on the session row: isolated
    -- with it, deleted with it and with the campaign. Added with an empty default, so no existing
    -- row changes.
    ALTER TABLE transcript_sessions ADD COLUMN speaker_names JSONB NOT NULL DEFAULT '{}';

    -- dmbot_web_transcript_speakers: the session's stored names and which of its speakers are DMs
    -- of the campaign (only people who appear in its lines, so a DM who never spoke is not named
    -- here). Any member of the server may ask, as for the transcript's lines; NULL when the caller
    -- is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_speakers(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'names', (SELECT t.speaker_names FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid),
            'dms', COALESCE((
                SELECT jsonb_agg(d.user_id::TEXT ORDER BY d.user_id)
                FROM campaign_dms d
                WHERE d.guild_id = g AND d.campaign_id = c
                  AND d.user_id IN (SELECT l.user_id FROM transcript_lines l
                                    WHERE l.guild_id = g AND l.session_id = sid)),
                '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_speakers(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

SHARED_RECAP_SESSION = """
    -- A shared recap says which session it was and how long it ran (#2418), the same two facts the
    -- DM's page shows. The public door reads them from the transcripts' own sessions, so the
    -- number is the one the transcripts show. It needs one more read-only policy, tied (like the
    -- others in RECAP_SHARE) to the one recap's server and campaign the function itself found;
    -- only the number and the length leave the function, never a line or a speaker.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON transcript_sessions FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())',
            current_user);
    END
    $do$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        sess_number BIGINT;
        sess_seconds BIGINT;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        -- Which session it was and how long it ran, as the transcripts page numbers and times it
        -- (`dmbot_web_transcript_sessions`: Session N in order of start among the campaign's saved
        -- sessions, those whose transcripts were deleted by retention included). Only these two
        -- numbers leave: nothing of the transcript itself.
        SELECT x.number, x.seconds INTO sess_number, sess_seconds FROM (
            SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number,
                   CASE WHEN t.ended_at IS NULL THEN NULL
                        ELSE GREATEST(t.ended_at - t.started_at, 0) END AS seconds
            FROM transcript_sessions t
            WHERE t.guild_id = job.guild_id AND t.campaign_id = job.campaign_id
              AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) x
        WHERE x.started_at = job.session_at ORDER BY x.number LIMIT 1;
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'sessionNumber', sess_number, 'sessionSeconds', sess_seconds,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

SHARE_UNPOSTED = """
    -- A recap's link for one the DM approved and has not posted (#2419: "Approve but don't post",
    -- then share it their own way). A link is still only made when the DM asks for it (the
    -- door's "on" and "new"), never by approving. It works while the recap is being posted or
    -- posted (the DM asked for the post, and the link would otherwise go dead for the minute it
    -- takes), or while it is a draft whose text is still exactly the text the DM approved: the
    -- public read checks that itself, so an edit after sharing hides the recap until it is
    -- approved again, and an unapproved draft never answers. Both doors are otherwise as before.
    CREATE OR REPLACE FUNCTION dmbot_recap_share(g BIGINT, c TEXT, at_ BIGINT, act TEXT, tok TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF act = 'off' THEN
            UPDATE recap_jobs SET share_token = NULL
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', NULL);
        END IF;
        IF NOT (
           job.status IN ('posting', 'posted')
           OR (job.status = 'draft' AND job.state = 'ready' AND job.approved_hash <> ''
               AND job.approved_hash = encode(sha256(convert_to(
                   job.title || E'\n' || job.summary, 'UTF8')), 'hex'))) THEN
            RETURN jsonb_build_object('status', 'not_posted', 'token', NULL);
        END IF;
        IF (act = 'new' OR job.share_token IS NULL)
           AND (tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$') THEN
            RETURN NULL;  -- the API always passes a good one
        END IF;
        IF act = 'new' OR job.share_token IS NULL THEN
            UPDATE recap_jobs SET share_token = tok
                WHERE guild_id = g AND campaign_id = c AND session_at = at_;
            RETURN jsonb_build_object('status', 'ok', 'token', tok);
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'token', job.share_token);
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        sess_number BIGINT;
        sess_seconds BIGINT;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND (
            status IN ('posting', 'posted')
            OR (status = 'draft' AND state = 'ready' AND approved_hash <> ''
                AND approved_hash = encode(sha256(convert_to(
                    title || E'\n' || summary, 'UTF8')), 'hex')));
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        -- Which session it was and how long it ran, as the transcripts page numbers and times it
        -- (`dmbot_web_transcript_sessions`: Session N in order of start among the campaign's saved
        -- sessions, those whose transcripts were deleted by retention included). Only these two
        -- numbers leave: nothing of the transcript itself.
        SELECT x.number, x.seconds INTO sess_number, sess_seconds FROM (
            SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number,
                   CASE WHEN t.ended_at IS NULL THEN NULL
                        ELSE GREATEST(t.ended_at - t.started_at, 0) END AS seconds
            FROM transcript_sessions t
            WHERE t.guild_id = job.guild_id AND t.campaign_id = job.campaign_id
              AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) x
        WHERE x.started_at = job.session_at ORDER BY x.number LIMIT 1;
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'sessionNumber', sess_number, 'sessionSeconds', sess_seconds,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_share(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

RECAP_ADDRESS_CO_DM = """
    -- A co-DM with no recap job of their own gets the recap email too (#2360): the address is
    -- answered for any CURRENT DM of a campaign that has a recap job or a held recap (a recap
    -- waiting for name answers, whose waiting email also goes to the DMs) in this server. Being the
    -- DM a job was made for is no longer enough: someone who has since stopped being a DM of the
    -- campaign gets nothing. Still one person's own address, still only while the recap worker's
    -- door is open. The rows read are this server's (forced row-level security), so no other
    -- server's DMs are seen.
    CREATE OR REPLACE FUNCTION dmbot_recap_address(who BIGINT) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found TEXT;
        before TEXT := COALESCE(current_setting('dmbot.recap_mail_lookup', true), '');
    BEGIN
        IF dmbot_recap_mail() IS DISTINCT FROM 'mail' THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM campaign_dms d
            WHERE d.user_id = who
              AND (EXISTS (SELECT 1 FROM recap_jobs j
                           WHERE j.guild_id = d.guild_id AND j.campaign_id = d.campaign_id)
                   OR EXISTS (SELECT 1 FROM recap_holds h
                              WHERE h.guild_id = d.guild_id AND h.campaign_id = d.campaign_id))
        ) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_mail_lookup', 'on', true);
        SELECT COALESCE(payment_email, email) INTO found FROM web_users WHERE user_id = who;
        PERFORM set_config('dmbot.recap_mail_lookup', before, true);
        RETURN found;
    END
    $fn$;
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_address(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_address(BIGINT) FROM PUBLIC;
"""

# The recap posting clock (#2435): when a post or a narration began, at which step, and the posted
# message's link, for the website's progress clock. New nullable columns: no row changes. The status
# change itself starts and stops the clock (a trigger), so every writer of `status` (the bot, the
# DM screen's buttons, the website) is covered at one place, and a failed or finished post can never
# leave a clock running. The audio worker sets and clears the 'audio' step (recaps/store.py).
RECAP_POSTING_CLOCK = r"""
    ALTER TABLE recap_jobs ADD COLUMN posting_since BIGINT CHECK (posting_since > 0);
    ALTER TABLE recap_jobs ADD COLUMN posting_step TEXT
        CHECK (posting_step IN ('check', 'post', 'audio'));
    ALTER TABLE recap_jobs ADD COLUMN posted_url TEXT
        CHECK (posted_url ~ '^https://discord\.com/channels/[0-9]+/[0-9]+/[0-9]+$'
               AND char_length(posted_url) <= 200);
    ALTER TABLE recap_jobs ADD CONSTRAINT recap_jobs_posting_pair
        CHECK ((posting_since IS NULL) = (posting_step IS NULL));

    CREATE FUNCTION dmbot_recap_posting_clock() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    BEGIN
        IF NEW.status = 'posting' AND OLD.status IS DISTINCT FROM 'posting' THEN
            NEW.posting_since := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
            NEW.posting_step := 'check';
            NEW.posted_url := NULL;
        ELSIF OLD.status = 'posting' AND NEW.status IS DISTINCT FROM 'posting' THEN
            NEW.posting_since := NULL;  -- posted, put back as a draft or dropped: the clock stops
            NEW.posting_step := NULL;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER recap_jobs_posting_clock BEFORE UPDATE OF status ON recap_jobs
        FOR EACH ROW EXECUTE FUNCTION dmbot_recap_posting_clock();
"""

RECAP_AUDIO_TOLD = """
    -- The DM is told once, on the DM screen, when a recap's audio is ready or has failed (#2621).
    -- `audio_told` holds the audio state that was told ('ready' or 'failed'); '' when none is. A
    -- trigger clears it each time the audio is queued, whichever door queued it (approval, the
    -- website's Make it again, the bot), so a new making is told again and a restart never repeats
    -- a notice. Recaps whose audio is already ready or failed are marked as told: no old news.
    ALTER TABLE recap_jobs ADD COLUMN audio_told TEXT NOT NULL DEFAULT '';
    CREATE POLICY migrate_backfill ON recap_jobs FOR ALL USING (true) WITH CHECK (true);
    UPDATE recap_jobs SET audio_told = audio WHERE audio IN ('ready', 'failed');
    DROP POLICY migrate_backfill ON recap_jobs;
    CREATE FUNCTION dmbot_recap_audio_told_clock() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    BEGIN
        IF NEW.audio = 'queued' AND OLD.audio IS DISTINCT FROM 'queued' THEN
            NEW.audio_told := '';
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER recap_jobs_audio_told BEFORE UPDATE OF audio ON recap_jobs
        FOR EACH ROW EXECUTE FUNCTION dmbot_recap_audio_told_clock();
"""

RECAP_AUDIO_REMAKE = """
    -- What the last making of a recap's audio did, and how far the one under way has got (#2630).
    -- `audio_how`: 'new' (made from nothing), 'voice' (only the changed paragraphs were spoken
    -- again, the music kept), 'all' (everything made again, `audio_why` says why: no_parts,
    -- paragraphs, moods or size) or 'same' (the spoken words had not changed). `audio_stage`,
    -- `audio_done` of `audio_total` and `audio_moved_at` are the live progress while the audio is
    -- queued: voice parts made, then music and mix. Codes and counts only; every column has a
    -- constant default, so no row changes.
    ALTER TABLE recap_jobs
        ADD COLUMN audio_how TEXT NOT NULL DEFAULT ''
            CHECK (audio_how IN ('', 'new', 'voice', 'all', 'same')),
        ADD COLUMN audio_why TEXT NOT NULL DEFAULT ''
            CHECK (audio_why IN ('', 'no_parts', 'paragraphs', 'moods', 'size')),
        ADD COLUMN audio_stage TEXT NOT NULL DEFAULT ''
            CHECK (audio_stage IN ('', 'voice', 'music', 'mix')),
        ADD COLUMN audio_done INT NOT NULL DEFAULT 0 CHECK (audio_done >= 0),
        ADD COLUMN audio_total INT NOT NULL DEFAULT 0 CHECK (audio_total >= 0),
        ADD COLUMN audio_moved_at BIGINT NOT NULL DEFAULT 0;
"""

BELIEFS = (
    f"""
    -- Assertions, perspectives and evidence (#2409; docs/STORY_MEMORY.md, section 9). A connection
    -- row is already the claim itself; this adds WHO believes it and how strongly.
    --   memory_perspectives  who holds beliefs: `truth` (the DM's meta-truth) and `party` (what
    --                        the players have learned) for every campaign, and optionally one per
    --                        player character (`pc`) or NPC (`npc`) whose beliefs matter.
    --   memory_beliefs       a perspective's belief in one connection: a confidence from 0.0
    --                        (certainly false) to 1.0, a status, the session, and the evidence it
    --                        points at BY ID (claim, line or event pointers; no words). History is
    --                        kept: a new belief adds a row, and the highest `seq` for a pair is
    --                        the current one (a write's memory version, or one more than the
    --                        pair's last, whichever is higher, so a restored backup keeps order).
    --   memory_entities.manipulated_by  an event, clue or item entry that points at the evidence
    --                        it faked ("the ledger Fabio planted").
    -- Like every memory table both are one campaign's, with forced row-level security and
    -- composite keys, so a row can never point into another campaign.
    CREATE TABLE memory_perspectives (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind       TEXT NOT NULL CHECK (kind IN ('truth', 'party', 'pc', 'npc')),
        entity_id  TEXT,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((kind IN ('truth', 'party') AND entity_id IS NULL)
            OR (kind IN ('pc', 'npc') AND entity_id IS NOT NULL)),
        {_entity_link("entity_id")}
    );
    CREATE UNIQUE INDEX memory_perspectives_one_each
        ON memory_perspectives (guild_id, campaign_id, kind) WHERE kind IN ('truth', 'party');
    CREATE UNIQUE INDEX memory_perspectives_one_per_entry
        ON memory_perspectives (guild_id, campaign_id, entity_id) WHERE entity_id IS NOT NULL;
    CREATE TABLE memory_beliefs (
        {_memory_scope()}
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        perspective_id TEXT NOT NULL,
        relation_id    TEXT NOT NULL,
        confidence     DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        status         TEXT NOT NULL CHECK (status IN ('theory', 'accusation', 'revealed_fact',
            'false_evidence', 'secret_plot', 'magically_verified', 'evasion_under_magic')),
        session_at     BIGINT,
        evidence_ids   TEXT[] NOT NULL DEFAULT '{{}}'
            CHECK (cardinality(evidence_ids) <= 20
               AND char_length(array_to_string(evidence_ids, ',')) <= 2000),
        seq            BIGINT NOT NULL,
        recorded_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX memory_beliefs_current
        ON memory_beliefs (guild_id, campaign_id, relation_id, perspective_id, seq DESC);
    CREATE INDEX memory_beliefs_by_perspective
        ON memory_beliefs (guild_id, campaign_id, perspective_id);
    ALTER TABLE memory_entities ADD COLUMN manipulated_by TEXT;
    CREATE INDEX memory_entities_by_manipulated_by
        ON memory_entities (guild_id, campaign_id, manipulated_by) WHERE manipulated_by IS NOT NULL;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_manipulated_by_other
        CHECK (manipulated_by IS NULL OR manipulated_by <> id);
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_manipulated_by_link
        FOREIGN KEY (guild_id, campaign_id, manipulated_by)
        REFERENCES memory_entities (guild_id, campaign_id, id) DEFERRABLE INITIALLY DEFERRED;
    """
    + _isolate("memory_perspectives")
    + _isolate("memory_beliefs")
    + """
    -- The backfill. Migrations run with no server set, so row-level security hides every row: open
    -- EVERY table the statements read or write for them alone, dropped again inside the
    -- migration's transaction (CLAUDE.md: a plain INSERT ... SELECT would match nothing).
    CREATE POLICY migrate_backfill ON memory_perspectives FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_beliefs FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    -- Every campaign gets The truth and The party.
    INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)
        SELECT c.guild_id, c.id, md5(c.guild_id::text || c.id || k.kind || clock_timestamp()::text),
               k.kind, extract(epoch FROM now())::BIGINT
          FROM campaigns c CROSS JOIN (VALUES ('truth'), ('party')) AS k(kind);
    -- Every confirmed connection: The truth at 1.0 (a secret one as a Secret Plot) ...
    INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,
                                confidence, status, session_at, evidence_ids, seq, recorded_at)
        SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'truth'), p.id, r.id, 1.0,
               CASE WHEN r.secret THEN 'secret_plot' ELSE 'revealed_fact' END,
               NULL, '{}', 0, extract(epoch FROM now())::BIGINT
          FROM memory_relations r
          JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                    AND p.kind = 'truth'
         WHERE r.status = 'confirmed';
    -- ... and, when it is not secret, The party at 1.0 (a Revealed Fact). A secret one gets no
    -- party belief at all.
    INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,
                                confidence, status, session_at, evidence_ids, seq, recorded_at)
        SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'party'), p.id, r.id, 1.0,
               'revealed_fact', NULL, '{}', 0, extract(epoch FROM now())::BIGINT
          FROM memory_relations r
          JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                    AND p.kind = 'party'
         WHERE r.status = 'confirmed' AND NOT r.secret;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_beliefs;
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON campaigns;
    -- Every campaign made from now on has them from the start, part of the campaign and never part
    -- of a change a DM could undo (the trigger runs in the creating transaction, so the server is
    -- already set and the table's policy lets the rows in).
    CREATE FUNCTION dmbot_campaign_perspectives() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)
            SELECT NEW.guild_id, NEW.id, md5(NEW.guild_id::text || NEW.id || k.kind), k.kind,
                   NEW.created_at
              FROM (VALUES ('truth'), ('party')) AS k(kind);
        RETURN NEW;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_campaign_perspectives()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    CREATE TRIGGER campaign_perspectives AFTER INSERT ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_campaign_perspectives();
    """
)

# The relationship web's door reads what The party believes (#2409 part B): a member sees the
# connections the players hold as fact, where it used to test the `secret` flag. Until guesses
# arrive the two always agree (the writers keep them so, and a test holds the door to it). The
# door is redefined as it was, with that one test changed, and its pinned settings set again.
_PARTY_KNOWS_R = (
    "EXISTS (SELECT 1 FROM memory_beliefs pb JOIN memory_perspectives pp"
    " ON pp.guild_id = pb.guild_id AND pp.campaign_id = pb.campaign_id"
    " AND pp.id = pb.perspective_id WHERE pp.kind = 'party' AND pb.guild_id = r.guild_id"
    " AND pb.campaign_id = r.campaign_id AND pb.relation_id = r.id AND pb.confidence >= 1.0"
    " AND pb.seq = (SELECT max(x.seq) FROM memory_beliefs x WHERE x.guild_id = pb.guild_id"
    " AND x.campaign_id = pb.campaign_id AND x.perspective_id = pb.perspective_id"
    " AND x.relation_id = pb.relation_id))"
)
assert WEB_RELATIONSHIPS.count("(is_dm OR (NOT r.secret AND r.to_session_at IS NULL") == 1
# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
WEB_GRAPH_PARTY = WEB_RELATIONSHIPS.replace(
    "CREATE FUNCTION dmbot_web_relationships(",
    "CREATE OR REPLACE FUNCTION dmbot_web_relationships(",
    1,
).replace(
    "(is_dm OR (NOT r.secret AND r.to_session_at IS NULL",
    "(is_dm OR (" + _PARTY_KNOWS_R + " AND r.to_session_at IS NULL",
    1,
)
SHARED_RECAP_SESSION = """
    -- A shared recap says which session it was and how long it ran (#2418), the same two facts the
    -- DM's page shows. The public door reads them from the transcripts' own sessions, so the
    -- number is the one the transcripts show. It needs one more read-only policy, tied (like the
    -- others in RECAP_SHARE) to the one recap's server and campaign the function itself found;
    -- only the number and the length leave the function, never a line or a speaker.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON transcript_sessions FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())',
            current_user);
    END
    $do$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        sess_number BIGINT;
        sess_seconds BIGINT;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        -- Which session it was and how long it ran, as the transcripts page numbers and times it
        -- (`dmbot_web_transcript_sessions`: Session N in order of start among the campaign's saved
        -- sessions, those whose transcripts were deleted by retention included). Only these two
        -- numbers leave: nothing of the transcript itself.
        SELECT x.number, x.seconds INTO sess_number, sess_seconds FROM (
            SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number,
                   CASE WHEN t.ended_at IS NULL THEN NULL
                        ELSE GREATEST(t.ended_at - t.started_at, 0) END AS seconds
            FROM transcript_sessions t
            WHERE t.guild_id = job.guild_id AND t.campaign_id = job.campaign_id
              AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) x
        WHERE x.started_at = job.session_at ORDER BY x.number LIMIT 1;
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'sessionNumber', sess_number, 'sessionSeconds', sess_seconds,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

# Players View / Full View on the relationship web, and the peek notice (#2404, slice 1).
# The web API says which view it wants through a transaction setting (`dmbot.web_view`: 'players'
# or 'full'; nothing means the person's own: full for a DM, players for anyone else). The door
# re-checks it every time, so the setting only ever asks for less than the person has, or for a
# peek the campaign's DM-screen visibility allows ('peek' or 'open'); a peek under 'private' is
# refused ('peek_off'). A DM in Players View sees exactly what a player sees, and cannot edit.
# `web_peeks` holds the once-for-each-visit notice the bot posts on the DM screen ("<name> peeked
# at the relationship web"); a visit is one website sign-in. Isolated per server, gone with the
# campaign, never read by the website's role except through the peek door, and left out of backups.
WEB_GRAPH_VIEWS = (
    """
    CREATE TABLE web_peeks (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        visit       TEXT NOT NULL CHECK (char_length(visit) BETWEEN 1 AND 100),
        name        TEXT NOT NULL CHECK (char_length(name) <= 100),
        created_at  BIGINT NOT NULL,
        posted_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, user_id, visit),
        FOREIGN KEY (guild_id, campaign_id) REFERENCES campaigns (guild_id, id)
            ON DELETE CASCADE
    );
    CREATE INDEX web_peeks_waiting ON web_peeks (guild_id, created_at) WHERE posted_at IS NULL;
    """
    + _isolate("web_peeks")
    + r"""
    -- Note a peek: the caller's name and visit, once. Internal (no grant): the peek door and the
    -- graph door call it, so a full view is never given to a player without the DM being told,
    -- whichever way it was asked for. TRUE the first time for this visit.
    CREATE FUNCTION dmbot_web_peek_note(g BIGINT, c TEXT, v TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        who TEXT;
        added INT;
    BEGIN
        IF v IS NULL OR v = '' OR char_length(v) > 100 THEN
            RETURN NULL;
        END IF;
        SELECT left(COALESCE(
                   NULLIF(btrim(regexp_replace(display_name, '[[:space:]]+', ' ', 'g')), ''),
                   'Someone'), 100) INTO who
            FROM web_users WHERE user_id = dmbot_current_user();
        INSERT INTO web_peeks (guild_id, campaign_id, user_id, visit, name, created_at)
            VALUES (g, c, dmbot_current_user(), v, COALESCE(who, 'Someone'),
                    floor(extract(epoch FROM clock_timestamp()))::BIGINT)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        RETURN added > 0;
    END
    $fn$;

    -- Peeked: the caller (not a DM of the campaign) opens the full view for this visit. 'ok' with
    -- `noted` true the first time for this visit; 'peek_off' under a private DM screen. NULL when
    -- the caller is not in the server or there is no such campaign.
    CREATE FUNCTION dmbot_web_peek(g BIGINT, c TEXT, v TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        vis TEXT;
        noted BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT dm_screen_visibility INTO vis FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF EXISTS (SELECT 1 FROM campaign_dms d WHERE d.guild_id = g AND d.campaign_id = c
                                                  AND d.user_id = dmbot_current_user()) THEN
            RETURN jsonb_build_object('status', 'ok', 'noted', false);  -- the DM needs no notice
        END IF;
        IF vis NOT IN ('peek', 'open') THEN
            RETURN jsonb_build_object('status', 'peek_off');
        END IF;
        noted := dmbot_web_peek_note(g, c, v);
        IF noted IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_visit');
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'noted', noted);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_peek(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_peek_note(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_peek(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_peek_note(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

_VIEW_STEPS = (
    # (old, new, how many times the old text is in the door)
    (
        "        is_dm BOOLEAN;\n        vis TEXT[];",
        "        is_dm BOOLEAN;\n        sees_all BOOLEAN;\n        wanted TEXT;\n"
        "        screen TEXT;\n        vis TEXT[];",
        1,
    ),
    (
        "                           AND d.user_id = dmbot_current_user());\n        vis := ARRAY(",
        """                           AND d.user_id = dmbot_current_user());
        SELECT dm_screen_visibility INTO screen FROM campaigns WHERE guild_id = g AND id = c;
        wanted := COALESCE(NULLIF(current_setting('dmbot.web_view', true), ''),
                           CASE WHEN is_dm THEN 'full' ELSE 'players' END);
        -- A DM sees everything unless they asked for Players View; anyone else sees the players'
        -- view unless they asked to peek and the DM screen lets them.
        sees_all := wanted = 'full' AND (is_dm OR screen IN ('peek', 'open'));
        IF wanted = 'full' AND NOT sees_all THEN
            RETURN jsonb_build_object('status', 'peek_off');
        END IF;
        -- A player's full view is a peek, however it was asked for: the DM is told once for this
        -- visit (the visit is the API's `dmbot.web_visit`); with none, the peek is refused.
        IF sees_all AND NOT is_dm
           AND dmbot_web_peek_note(g, c, current_setting('dmbot.web_visit', true)) IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_visit');
        END IF;
        vis := ARRAY(""",
        1,
    ),
    ("AND (is_dm OR (NOT e.audience_limited", "AND (sees_all OR (NOT e.audience_limited", 1),
    (
        "              AND (is_dm OR (" + _PARTY_KNOWS_R,
        "              AND (sees_all OR (" + _PARTY_KNOWS_R,
        1,
    ),
    (
        "'canEdit', is_dm,",
        "'canEdit', is_dm AND sees_all, 'seesAll', sees_all,\n"
        "                                      'view',\n"
        "                                      CASE WHEN sees_all THEN 'full' ELSE 'players' END,\n"
        "                                      'visibility', screen,\n"
        "                                      'canPeek', is_dm OR screen IN ('peek', 'open'),",
        2,
    ),
    ("'secret', CASE WHEN is_dm THEN EXISTS (", "'secret', CASE WHEN sees_all THEN EXISTS (", 1),
)
_door = WEB_GRAPH_PARTY
for _old, _new, _times in _VIEW_STEPS:
    assert _door.count(_old) == _times, _old
    _door = _door.replace(_old, _new)
WEB_GRAPH_VIEWS = WEB_GRAPH_VIEWS + _door

SESSION_PAUSES = """
    -- Off the record (#2466): the pauses that are on, kept with the saved session so a restart
    -- that resumes the session resumes them too. {"people": {"<user id>": <ends at, Unix seconds
    -- or null for until resumed>}, "table": <ends at or null>}. A new column with a default, so no
    -- row changes; the session's row already holds this server's data only.
    ALTER TABLE active_sessions ADD COLUMN paused JSONB NOT NULL DEFAULT '{}';
"""

OFF_RECORD_LINES = """
    -- A stretch where a person was off the record (#2466) is one marker line in the transcript,
    -- in both versions: "(Mia was off the record, nothing recorded, 20:14-20:19 UTC)". It is
    -- stored as a line of its
    -- own kind under that person (so a consent stop or "Delete my past transcripts" takes it
    -- with their other lines) and holds no speech: when it began, how long it lasted, and the
    -- words "off the record". The one change is the list of kinds the column accepts.
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_check
        CHECK (sidebar IN ('question', 'answer', 'table_question', 'off_record'));
"""

# A connection the DM's story holds back from the players until it is said at the table (#2404):
# a backstory connection starts unrevealed. The column has a default and is added, not updated,
# so every existing connection reads as revealed and no row-level security needs opening. The
# party's belief in it (`beliefs.follow`) stays the one thing the players' readers look at; this
# flag only decides whether `follow` gives the party that belief. The graph door also says, for
# the full view, which names and connections the players have not been shown yet.
UNREVEALED_CONNECTIONS = """
    ALTER TABLE memory_relations ADD COLUMN unrevealed BOOLEAN NOT NULL DEFAULT false;
    """ + (
    _door.replace(
        "'id', e.id, 'name', e.name,\n",
        "'id', e.id, 'name', e.name,\n"
        "                    'revealed', NOT (e.hidden_from_players OR e.audience_limited),\n",
        1,
    ).replace(
        "'detail', r.detail, 'secret', r.secret, 'at', r.created_at)",
        "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,\n"
        "                    'revealed', " + _PARTY_KNOWS_R + ")",
        1,
    )
)
assert UNREVEALED_CONNECTIONS.count("'revealed', NOT (e.hidden") == 1
assert UNREVEALED_CONNECTIONS.count("'revealed', EXISTS") == 1

MERGE_RESTORES = f"""
    -- Where a merge moved a name's private notes and D&D Beyond sheet (#2438). Both live outside
    -- the change log (a note is its writer's own, a sheet is refreshed by its player), so Undo had
    -- no way to hand them back to the name they came from. One row per merged-away name per
    -- batch: lengths and times of the notes and the sheet row as they were before the merge, never
    -- a note's words (a person who deletes their notes leaves no copy). Undo reads it,
    -- puts back only what is still as the merge left it, and never refuses because of it. Kept
    -- as long as the batch is (pruned with it), deleted with the campaign.
    CREATE TABLE merge_restores (
        {_memory_scope()}
        id       BIGSERIAL,
        batch    BIGINT NOT NULL,
        -- 'undo': what an Undo of the batch puts back; 'redo': the batch is an Undo, and undoing
        -- it merges these two again (the notes and sheet follow).
        kind     TEXT NOT NULL CHECK (kind IN ('undo', 'redo')),
        gone_id  TEXT NOT NULL,
        keep_id  TEXT NOT NULL,
        before   JSONB NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX merge_restores_by_batch ON merge_restores (guild_id, campaign_id, batch);
    """ + _isolate("merge_restores")

# The website's recap check agrees with the bot's (#2404, Supervisor 2026-10-22): a name the players
# haven't been shown yet counts as secret for a recap unless the table said it in that session. The
# check's own door (`dmbot_recap_check_names`) keeps listing such names as secret; this new door,
# for a DM of the campaign, says which of them there are and hands back only the session's table
# lines that could contain one (a loose prefilter; the API reads them exactly, as the bot does).
# Used for this one text and never returned. A new function: no row changes.
RECAP_HELD_NAMES = r"""
    CREATE FUNCTION dmbot_recap_held_names(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        held JSONB;
        hard JSONB;
        said JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- names of entries the players haven't been shown (not a clue, not a person), and the
        -- texts that stay secret whatever was said (a secret spelling, a clue's names)
        held := COALESCE((
            SELECT jsonb_agg(DISTINCT a.text)
            FROM memory_aliases a JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND e.hidden_from_players
              AND NOT e.audience_limited AND NOT a.secret AND a.status = 'confirmed'
              AND e.status = 'confirmed' AND e.type NOT IN ('person', 'clue')), '[]'::jsonb);
        hard := COALESCE((
            SELECT jsonb_agg(DISTINCT a.text)
            FROM memory_aliases a JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND (a.secret OR e.audience_limited)),
            '[]'::jsonb);
        said := COALESCE((
            SELECT jsonb_agg(l.heard || ' ' || COALESCE(l.text, ''))
            FROM (SELECT l2.heard, l2.text FROM transcript_lines l2
                  JOIN transcript_sessions s ON s.id = l2.session_id AND s.guild_id = l2.guild_id
                  WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
                    AND l2.sidebar IS NULL
                    AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(held) h(t)
                        WHERE position(
                                regexp_replace(lower(h.t), '[''’ʼ`-]', '', 'g') IN
                                regexp_replace(lower(l2.heard || ' ' || COALESCE(l2.text, '')),
                                               '[''’ʼ`-]', '', 'g')) > 0)
                  ORDER BY l2.started_ms, l2.id LIMIT 2000) l), '[]'::jsonb);
        RETURN jsonb_build_object('held', held, 'hard', hard, 'said', said);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_held_names(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_held_names(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

BACKSTORY_LINK_WORDS = """
    -- A stated relationship no fixed kind fits is kept on the catch-all with the words the text
    -- used (#2454), so a waiting connection has to carry them until the DM keeps it.
    ALTER TABLE backstory_links ADD COLUMN detail TEXT NOT NULL DEFAULT '';
"""

# The DM's 📌 100% truth (#2447): a flag on The truth's belief in a connection (a new belief row
# each time it is set or cleared, so the change log and Undo work as for any belief), and the flag
# a connection waiting from a campaign-lore upload carries until the DM keeps it. Both columns have
# defaults, so no row changes. The relationship web's door tells a DM, and only a DM seeing
# everything, which connections are pinned.
_PINNED_LINK = (
    "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,\n"
    "                    'revealed', " + _PARTY_KNOWS_R + ")"
)
_door_pinned = UNREVEALED_CONNECTIONS[
    UNREVEALED_CONNECTIONS.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships") :
]
assert _door_pinned.count(_PINNED_LINK) == 1
PINNED_TRUTH = """
    ALTER TABLE memory_beliefs ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE backstory_links ADD COLUMN truth BOOLEAN NOT NULL DEFAULT false;
    """ + _door_pinned.replace(
    _PINNED_LINK,
    _PINNED_LINK[:-1] + ",\n                    'pinned', (is_dm AND sees_all) AND EXISTS"
    " (SELECT 1 FROM memory_beliefs tb"
    " JOIN memory_perspectives tp ON tp.guild_id = tb.guild_id"
    " AND tp.campaign_id = tb.campaign_id AND tp.id = tb.perspective_id"
    " WHERE tp.kind = 'truth' AND tb.guild_id = r.guild_id AND tb.campaign_id = r.campaign_id"
    " AND tb.relation_id = r.id AND tb.pinned AND tb.seq = (SELECT max(x.seq)"
    " FROM memory_beliefs x WHERE x.guild_id = tb.guild_id AND x.campaign_id = tb.campaign_id"
    " AND x.perspective_id = tb.perspective_id AND x.relation_id = tb.relation_id)))",
    1,
)

# Family roles (#2457, Supervisor's ruling A): whether a family connection's detail is a role that
# reads one way ("Mira is the niece of Aldric"). Older rows are false: their detail was stored with
# the two names sorted by id, so its direction is arbitrary and they keep reading "is family of".
# The column has a default and is added, not updated, so no row changes and no backfill policy.
# The website's door sends it with each connection, built from the door as it stands now (0190).
_DIRECTIONAL_LINK = "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,"
_door_directional = PINNED_TRUTH[
    PINNED_TRUTH.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships") :
]
assert _door_directional.count(_DIRECTIONAL_LINK) == 1
RELATION_DIRECTIONAL = """
    ALTER TABLE memory_relations ADD COLUMN directional BOOLEAN NOT NULL DEFAULT false;
    """ + _door_directional.replace(
    _DIRECTIONAL_LINK,
    "'detail', r.detail, 'directional', r.directional, 'secret', r.secret, 'at', r.created_at,",
    1,
)

LINK_PIN_ACTION = """
    -- The website's 📌 pin (#2447): a DM pins a connection as 100% truth, or takes it back, through
    -- the same queue as the other connection edits ('link-pin': `target_id` the connection, `flag`
    -- pinned or not). The bot applies it with the call the name card uses, so the change log and
    -- Undo agree. No row changes: a wider list of actions and the door that checks it.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin'));
"""
# Built from the door as it stands now, with `merge-many` (0183), not from the older text: a door
# built from before that would drop it (found in the review of #2456).
_door_start = MERGE_MANY.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(")
_door_end = MERGE_MANY.index("    DO $do$", _door_start)
_pin_door = MERGE_MANY[_door_start:_door_end]
for _old, _new, _times in (
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove')",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        3,
    ),
    (
        "'hide', 'merge', 'remove', 'link-secret', 'link-remove')",
        "'hide', 'merge', 'remove', 'link-secret', 'link-remove', 'link-pin')",
        1,
    ),
):
    assert _pin_door.count(_old) == _times, (_old, _pin_door.count(_old))
    _pin_door = _pin_door.replace(_old, _new)
assert _pin_door.count("('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL") == 1
_pin_door = _pin_door.replace(
    "('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL",
    "('secret', 'hide', 'link-add', 'link-secret', 'link-pin') AND fl IS NULL",
)
LINK_PIN_ACTION = (
    LINK_PIN_ACTION
    + _pin_door
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""
)

WAITING_ACTIONS = (
    "claim-confirm", "claim-reject", "backstory-keep", "backstory-skip", "backstory-keep-all",
)  # fmt: skip
_WAITING_LIST = ", ".join(f"'{a}'" for a in WAITING_ACTIONS)
_WAITING_NEED_TARGET = ", ".join(f"'{a}'" for a in WAITING_ACTIONS[:4])  # keep-all has none
_KEEP_ALL_IDS = "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'"  # Keep all names the ids the page showed

LINK_DECIDE_ACTIONS = (
    """
    -- The website's answers to the connections that wait for the DM (#2635, #2642), through the
    -- queue of the other name actions. PlotBot's waiting connections are of two kinds, and neither
    -- is a proposed row of `memory_relations`:
    --   'claim-confirm' / 'claim-reject': `target_id` is a waiting `memory_claims` row (said at the
    --       table); `flag` on a confirm is "truth but secret". The bot makes the same call as the
    --       DM screen's ✅ and ❌ (`ClaimStore.confirm` / `reject`), so consent, the plan and
    --       instruction checks, the DM's own word and Undo are the same.
    --   'backstory-keep' / 'backstory-skip': `target_id` is a waiting `backstory_links` row (from
    --       the DM's own backstory); `flag` on a keep is "keep as secret". 'backstory-keep-all'
    --       keeps every one that is ready (no target). The bot keeps them as the one-by-one cards
    --       do (the DM's word, unrevealed), and answers how many in `result_id`.
    -- `entity_id` is empty for these: they belong to no one name. A backstory link gets an id of
    -- its own, so the page and the queue can point at one. No existing row changes (the column has
    -- a default, filled for old rows by the table rewrite).
    ALTER TABLE backstory_links
        ADD COLUMN id TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$');
    CREATE UNIQUE INDEX backstory_links_id ON backstory_links (guild_id, campaign_id, id);
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         """
    + _WAITING_LIST
    + """));
    -- Keep all carries the ids of the connections the page showed (at most 50 of 32 letters, space
    -- apart), so a link that turned ready while the DM was looking is not kept unseen.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all') THEN 1700 ELSE 100 END);
"""
)
# Built from the door as it stands now (with `link-pin`, 0193), not from an older text: a door
# built from before would drop the actions added since (the lesson of #2456).
_decide_door = _pin_door
_waiting_check = """        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' THEN
            -- a waiting connection: it must still be waiting in this campaign (the bot checks
            -- again when it applies it, and both of its ends are confirmed names then)
            subject := '';
            IF act LIKE 'claim-%' AND NOT EXISTS (
                   SELECT 1 FROM memory_claims m WHERE m.guild_id = g AND m.campaign_id = c
                   AND m.id = tgt AND m.status = 'proposed') THEN
                RETURN jsonb_build_object('status', 'not_found');
            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (
                   SELECT 1 FROM backstory_links b WHERE b.guild_id = g AND b.campaign_id = c
                   AND b.id = tgt) THEN
                RETURN jsonb_build_object('status', 'not_found');
            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (
                   SELECT 1 FROM backstory_links b WHERE b.guild_id = g AND b.campaign_id = c
                   AND b.id = ANY (string_to_array(clean, ' '))) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
        ELSIF is_link AND act <> 'link-add' THEN"""
for _old, _new in (
    (
        "                'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        f"                'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        f"                {_WAITING_LIST})",
    ),
    (
        "'link-secret', 'link-remove', 'link-pin')\n               AND clean <> '')",
        f"'link-secret', 'link-remove', 'link-pin',\n"
        f"                       {_WAITING_NEED_TARGET})\n               AND clean <> '')",
    ),
    (
        "OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        "OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        f"                       {_WAITING_NEED_TARGET})",
    ),
    (
        "OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', "
        "'link-pin')",
        "OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', "
        f"'link-pin',\n                       {_WAITING_NEED_TARGET})",
    ),
    (
        "('secret', 'hide', 'link-add', 'link-secret', 'link-pin') AND fl IS NULL",
        "('secret', 'hide', 'link-add', 'link-secret', 'link-pin', 'claim-confirm',\n"
        "                       'backstory-keep') AND fl IS NULL",
    ),
    ("        IF is_link AND act <> 'link-add' THEN", _waiting_check),
    (
        "char_length(clean) > (CASE WHEN act = 'merge-many' THEN 1700 ELSE 100 END)",
        "char_length(clean) > (CASE WHEN act IN ('merge-many', 'backstory-keep-all')"
        " THEN 1700 ELSE 100 END)",
    ),
    (
        "OR (act = 'merge-many' AND (clean = '' OR clean !~",
        "OR (act = 'backstory-keep-all' AND (clean = '' OR clean !~ " + _KEEP_ALL_IDS + "))\n"
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~",
    ),
):
    assert _decide_door.count(_old) == 1, (_old, _decide_door.count(_old))
    _decide_door = _decide_door.replace(_old, _new)
LINK_DECIDE_ACTIONS = (
    LINK_DECIDE_ACTIONS
    + _decide_door
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""
)
# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
CAMPAIGN_FACTS = """
    -- The facts on each campaign's card on My Account (docs/PLAN.md, "Where everything is on the
    -- website"; #2460): for one server, every campaign the signed-in person runs (a DM of it) or
    -- plays in (claimed a player character), in one read, not one per campaign. NULL when the
    -- caller is not in the server, like the other web doors. A non-DM's NPC count leaves out what
    -- the players cannot see (a hidden entry, or one whose own name is secret), exactly as the
    -- Names list does. `live` is DMbot's own record of the session it is in right now
    -- (active_sessions), not an open transcript row: a crash leaves one of those open. The seconds
    -- count the sessions that ended, those whose transcripts were deleted by retention included.
    CREATE FUNCTION dmbot_web_campaign_facts(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', x.id, 'name', x.name, 'dm', x.is_dm,
                'startedAt', x.created_at, 'lastPlayedAt', x.last_played_at,
                'live', x.live, 'sessions', x.sessions, 'secondsPlayed', x.seconds,
                'players', x.players, 'npcs', x.npcs) ORDER BY lower(x.name), x.id)
            FROM (
                SELECT k.id, k.name, k.created_at, k.last_played_at, d.user_id IS NOT NULL AS is_dm,
                    EXISTS (SELECT 1 FROM active_sessions a
                            WHERE a.guild_id = g AND a.campaign_id = k.id) AS live,
                    (SELECT count(*) FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = k.id
                        AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) AS sessions,
                    COALESCE((SELECT sum(GREATEST(t.ended_at - t.started_at, 0))
                              FROM transcript_sessions t
                              WHERE t.guild_id = g AND t.campaign_id = k.id
                                AND t.ended_at IS NOT NULL
                                AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)),
                             0) AS seconds,
                    (SELECT count(*) FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = k.id AND e.status = 'confirmed'
                        AND e.role = 'player_character' AND e.played_by IS NOT NULL) AS players,
                    (SELECT count(*) FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = k.id AND e.status = 'confirmed'
                        AND e.type = 'character' AND e.role = 'npc'
                        AND (d.user_id IS NOT NULL
                             OR (NOT e.audience_limited AND NOT e.hidden_from_players
                                 AND NOT EXISTS (
                                     SELECT 1 FROM memory_aliases a
                                     WHERE a.guild_id = g AND a.campaign_id = k.id
                                       AND a.entity_id = e.id AND a.status <> 'rejected'
                                       AND a.secret
                                       AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                         = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                                 )))) AS npcs
                FROM campaigns k
                LEFT JOIN campaign_dms d
                  ON d.guild_id = k.guild_id AND d.campaign_id = k.id AND d.user_id = me
                WHERE k.guild_id = g
                  AND (d.user_id IS NOT NULL
                       OR EXISTS (SELECT 1 FROM memory_entities e
                                  WHERE e.guild_id = g AND e.campaign_id = k.id
                                    AND e.status = 'confirmed' AND e.role = 'player_character'
                                    AND e.played_by = me))
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_facts(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_facts(BIGINT) FROM PUBLIC;
"""

SHARED_RULES_PAGE = """
    -- The Shared rules page on My Account (docs/PLAN.md, "Where everything is on the website";
    -- #2460 part 2): for one server, each campaign the signed-in person is a DM of that publishes a
    -- house-rules set or homebrew pack, or follows one, in one read. Only the DMs of a campaign see
    -- its sharing (the same people who can change it in Discord); a player gets nothing. It says
    -- whether a share code is on, never the code itself: the code is the way in to the set, so it
    -- is read in Discord. NULL when the caller is not in the server, like the other web doors.
    CREATE FUNCTION dmbot_web_shared_rules(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', k.id, 'name', k.name,
                'publishes', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'kind', s.kind, 'name', s.name, 'shared', s.status = 'shared',
                        'version', COALESCE((SELECT max(v.version) FROM rule_set_versions v
                                             WHERE v.guild_id = g AND v.campaign_id = k.id
                                               AND v.set_id = s.id), 1),
                        'hasCode', EXISTS (SELECT 1 FROM rule_set_codes c
                                           WHERE c.guild_id = g AND c.campaign_id = k.id
                                             AND c.set_id = s.id AND c.active))
                        ORDER BY s.kind, lower(s.name), s.id)
                    FROM rule_sets s WHERE s.guild_id = g AND s.campaign_id = k.id), '[]'::jsonb),
                'follows', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'kind', f.kind, 'name', f.set_name, 'publisher', f.publisher,
                        'version', f.version, 'updating', f.updating)
                        ORDER BY f.kind, f.position, lower(f.set_name), f.set_id)
                    FROM rule_set_follows f WHERE f.guild_id = g AND f.campaign_id = k.id),
                    '[]'::jsonb)
            ) ORDER BY lower(k.name), k.id)
            FROM campaigns k
            JOIN campaign_dms d
              ON d.guild_id = k.guild_id AND d.campaign_id = k.id AND d.user_id = me
            WHERE k.guild_id = g
              AND (EXISTS (SELECT 1 FROM rule_sets s WHERE s.guild_id = g AND s.campaign_id = k.id)
                   OR EXISTS (SELECT 1 FROM rule_set_follows f
                              WHERE f.guild_id = g AND f.campaign_id = k.id))), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_shared_rules(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_shared_rules(BIGINT) FROM PUBLIC;
"""

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
STORY_TIMELINE = """
    -- What the Story timeline page needs besides the sessions themselves (#2462): the campaign's
    -- recaps and the names shown to the players at the table, each with the time it belongs to. The
    -- sessions and their numbers come from the transcripts page's own door, so there is one count.
    -- For one campaign of a server in the caller's own list, NULL otherwise (and for a campaign
    -- that is not there). Recaps: a game master sees every one that is approved or going out or
    -- posted; a player sees posted ones only, never a draft. Shown at the table: the names DMbot
    -- showed the players after a session (the 'scan' writer's changes, #2404) that the players can
    -- still see: an Undo, or the game master hiding the name again, puts the entry back to hidden,
    -- and a redo shows it again, so the entry's own state says it (no need to follow the undo
    -- chain). The same list for everyone: they are names the players already see. A name whose own
    -- spelling is a secret one is left out for a player.
    CREATE FUNCTION dmbot_web_story_timeline(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me);
        RETURN jsonb_build_object(
            'dm', is_dm,
            'recaps', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'sessionAt', r.session_at, 'title', r.title, 'status', r.status)
                    ORDER BY r.session_at)
                FROM recap_jobs r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.title <> ''
                  AND r.status <> 'dropped'
                  AND (r.status = 'posted'
                       OR (is_dm AND (r.status = 'posting' OR r.approved_at > 0)))
            ), '[]'::jsonb),
            'reveals', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('at', x.at, 'names', x.names) ORDER BY x.at)
                FROM (
                    SELECT min(ch.made_at) AS at,
                           jsonb_agg(e.name ORDER BY lower(e.name), e.id) AS names
                    FROM memory_changes ch
                    JOIN memory_entities e
                      ON e.guild_id = ch.guild_id AND e.campaign_id = ch.campaign_id
                     AND e.id = ch.row_id
                    WHERE ch.guild_id = g AND ch.campaign_id = c
                      AND ch.table_name = 'memory_entities' AND ch.op = 'update'
                      AND ch.source = 'scan'
                      AND ch.before ->> 'hidden_from_players' = 'true'
                      AND ch.after ->> 'hidden_from_players' = 'false'
                      AND e.status = 'confirmed' AND e.type <> 'person'
                      AND NOT e.hidden_from_players AND NOT e.audience_limited
                      -- the DM's plot notes are not names, and a player never gets them
                      AND (is_dm OR e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                   'promise', 'deed'))
                      AND (is_dm OR NOT EXISTS (
                            SELECT 1 FROM memory_aliases a
                            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                              AND a.status <> 'rejected' AND a.secret
                              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')))
                    GROUP BY ch.batch
                ) x
            ), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_story_timeline(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_story_timeline(BIGINT, TEXT) FROM PUBLIC;
"""

ALL_NAMES_SAID = """
    -- How to say a name, for the All names page's row editor (#2317). The list door
    -- (dmbot_web_all_names) is not touched; this one answers, for a DM and for the entries just
    -- listed, the id of the entry's main name (the confirmed, public name that reads the same as
    -- the entry's own, as dmbot_web_set_said_as accepts it) and how the narrator says it. NULL for
    -- anyone who is not a DM of the campaign, so a manager never learns a spelling, and an entry
    -- with no such name is simply left out.
    CREATE FUNCTION dmbot_web_all_names_said(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        -- (a page is 20 names; a longer list is no page's, so it gets nothing)
        IF cardinality(ids) > 100
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', m.entity_id, 'aliasId', m.alias_id, 'saidAs', m.said_as))
            FROM (
                SELECT DISTINCT ON (e.id) e.id AS entity_id, a.id AS alias_id, a.said_as
                FROM memory_entities e
                JOIN memory_aliases a
                  ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id
                 AND a.entity_id = e.id
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids)
                  AND e.status = 'confirmed' AND e.type <> 'person'
                  AND NOT a.secret AND a.status = 'confirmed'
                  AND a.kind <> 'misheard' AND a.used_by IS NULL
                  AND lower(a.text) = lower(e.name)
                ORDER BY e.id, a.id
            ) m), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_all_names_said(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_all_names_said(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
"""

SECRET_EXPOSURES = f"""
    -- Secrets said or typed at the table, waiting for the DM to be told (#2410, docs/PLAN.md,
    -- "Secrets said out loud are revealed too, and the DM can recant"). One row for each secret
    -- name or secret connection found in a session's recorded table talk. Recorded and not
    -- applied: nothing becomes known to the players here, and the summary that tells the DM
    -- applies it later. Holds which secret and how often, never a line or who said it. A name is
    -- kept as its spelling key, a connection as the connection's id. 'settled' once the DM
    -- answered it another way. Transient state, so it is left out of backups.
    CREATE TABLE secret_exposures (
        {_memory_scope()}
        id                 BIGSERIAL,
        session_started_at BIGINT NOT NULL,
        kind               TEXT NOT NULL CHECK (kind IN ('name', 'connection')),
        spelling           TEXT CHECK (spelling IS NULL OR char_length(spelling) BETWEEN 1 AND 400),
        relation_id        TEXT,
        times              INTEGER NOT NULL CHECK (times > 0),
        status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'settled')),
        created_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((kind = 'name' AND spelling IS NOT NULL AND relation_id IS NULL)
            OR (kind = 'connection' AND relation_id IS NOT NULL AND spelling IS NULL)),
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX secret_exposures_name
        ON secret_exposures (guild_id, campaign_id, session_started_at, spelling)
        WHERE kind = 'name';
    CREATE UNIQUE INDEX secret_exposures_connection
        ON secret_exposures (guild_id, campaign_id, session_started_at, relation_id)
        WHERE kind = 'connection';
    """ + _isolate("secret_exposures")

EXPOSURE_SUMMARY = """
    -- The after-session summary applies what a session gave away (#2410, slice 2): an exposure
    -- becomes 'applied' (the secret was shown to the players, in the change-log batch kept here so
    -- Recant can undo exactly that) or 'recanted' (the DM took it back). New states and a column
    -- with no default needed: no row changes. 'applying' is a row claimed while its secret is being
    -- shown, so a Recant pressed meanwhile cannot slip between the claim and the showing.
    ALTER TABLE secret_exposures ADD COLUMN batch BIGINT;
    ALTER TABLE secret_exposures ADD COLUMN claimed_at BIGINT;
    ALTER TABLE secret_exposures DROP CONSTRAINT secret_exposures_status_check;
    ALTER TABLE secret_exposures ADD CONSTRAINT secret_exposures_status_check
        CHECK (status IN ('pending', 'settled', 'applying', 'applied', 'recanted'));
"""

RECAP_CALL_LOG = """
    -- When each AI call of a recap job was made (UTC seconds), kept for two days, so the campaign's
    -- daily tally counts only the last 24 hours of calls and not a waiting recap's whole lifetime
    -- (#2308). A job from before this has none: it is counted as it was (its `spent`, while it was
    -- updated in the last day). The column has a default and is added, not updated: no row changes.
    ALTER TABLE recap_jobs ADD COLUMN spent_log BIGINT[] NOT NULL DEFAULT '{}';
"""

TIMELINE_STABLE = """
    -- The story timeline's door only reads (#2462), so it is STABLE: the planner may reuse its
    -- answer inside one statement. 0196 created it VOLATILE, the default; changing the volatility
    -- keeps its pinned search_path, its owner and its grants. No rows change.
    ALTER FUNCTION dmbot_web_story_timeline(BIGINT, TEXT) STABLE;
"""

WEB_IMAGE_LINES = """
    -- The website's transcript lines say which line describes a picture (#2507, after #2450): the
    -- reading page and the downloads tag it "(image)", as the bot's own file does, never "(typed)".
    -- The door is the one WEB_TRANSCRIPT_PAGES made, with one more key on each line. No rows
    -- change.
"""
_lines_start = WEB_TRANSCRIPT_PAGES.index("    CREATE FUNCTION dmbot_web_transcript_lines(")
_lines_end = WEB_TRANSCRIPT_PAGES.index("    $fn$;", _lines_start) + len("    $fn$;")
_lines_door = WEB_TRANSCRIPT_PAGES[_lines_start:_lines_end].replace(
    "CREATE FUNCTION dmbot_web_transcript_lines(",
    "CREATE OR REPLACE FUNCTION dmbot_web_transcript_lines(",
    1,
)
assert _lines_door.count("'typed', p.typed)") == 1
WEB_IMAGE_LINES += (
    _lines_door.replace("'typed', p.typed)", "'typed', p.typed, 'image', p.image)")
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)
        FROM PUBLIC;
"""
)

IMAGE_LINES = """
    -- A transcript line that describes an image posted in the table chat (#2450, docs/PLAN.md,
    -- "Images posted there"): stored as a typed line (everything that treats typed lines apart
    -- keeps doing so) and tagged "(image)" in the downloads. The image itself is never stored,
    -- only the words. Defaulted: no row changes.
    ALTER TABLE transcript_lines ADD COLUMN image BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_image_is_typed
        CHECK (NOT image OR typed);

    -- How many pictures a campaign has had described on a day (UTC day number), so the day's
    -- limit holds across restarts and across copies of the bot. A count only: no picture, no
    -- words, no one's name. Rows go with their campaign; older days are cleared as new ones are
    -- counted. New table: no rows change, so no backfill policy.
    CREATE TABLE image_day_use (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        day         INTEGER NOT NULL,
        used        INTEGER NOT NULL CHECK (used >= 0),
        PRIMARY KEY (guild_id, campaign_id, day),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """ + _isolate("image_day_use")

RECAP_APPROVE_UNCHECKED = """
    -- "Approve it as it is" for a recap whose critics could not be reached for 3 days (#2305).
    -- The website's role may not write recap_jobs, so this is the door: only a DM of the campaign,
    -- only a draft still in the critics' loop that has waited at least `wait_s` seconds for its
    -- check (the caller passes the 3 days, so the number lives in one place), and only the text
    -- the DM read: `expected` is the hash of the title and summary the page showed, and a draft
    -- the worker has revised since approves nothing ('changed'). A second press after an approval
    -- answers 'approved' again and changes nothing. It marks the loop done, records who and when
    -- in the loop's JSON, adds the note that the draft was never fully checked, and lets the
    -- worker run on to the title step (next_at = 0), which makes it ready.
    -- A new function: no rows change, so no row-level-security policy is needed.
    CREATE FUNCTION dmbot_recap_approve_unchecked(
        g BIGINT, c TEXT, at_ BIGINT, wait_s BIGINT, note TEXT, expected TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        since_s BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.status <> 'draft' OR job.state <> 'prose'
           OR job.critics IS NULL OR job.summary = '' THEN
            RETURN 'not_allowed';
        END IF;
        IF COALESCE((job.critics->>'approved_at')::BIGINT, 0) > 0 THEN
            RETURN 'approved';  -- already approved: a second press changes nothing
        END IF;
        IF COALESCE(job.critics->>'done', 'false') = 'true' THEN
            RETURN 'not_allowed';
        END IF;
        IF expected <> encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                              'hex') THEN
            RETURN 'changed';
        END IF;
        -- Waiting for the check at all (a wait was counted), and for long enough.
        since_s := COALESCE((job.critics->>'since')::BIGINT, 0);
        IF COALESCE((job.critics->>'waits')::BIGINT, 0) = 0 OR since_s = 0
           OR now_s - since_s < wait_s THEN
            RETURN 'not_yet';
        END IF;
        UPDATE recap_jobs SET
            critics = job.critics || jsonb_build_object(
                'done', true, 'approved_by', dmbot_current_user(), 'approved_at', now_s,
                'notes', CASE WHEN jsonb_typeof(job.critics->'notes') = 'array'
                              THEN job.critics->'notes' ELSE '[]'::jsonb END || to_jsonb(note)),
            last_error = '', errors = 0, next_at = 0
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'approved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_approve_unchecked(BIGINT, TEXT, BIGINT, BIGINT, TEXT, TEXT)
        FROM PUBLIC;
    """

MAP_NOTES = """
    -- A map someone posted in the table chat, put into words for MapBot, a later feature (#2450,
    -- docs/PLAN.md, "Images posted there"): the places on it and where each is from the others, and
    -- the layout as a whole. Words only, never the picture. `user_id` is who posted it, so the
    -- notes go when that person deletes their lines. Rows go with their campaign; a campaign keeps
    -- at most 200 (the store drops the oldest). New table: no rows change, so no backfill policy.
    CREATE TABLE map_notes (
        id          BIGSERIAL PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        -- the Discord message the map was posted in, so deleting the picture deletes its note
        -- (0: restored from a backup, which never carries message IDs)
        message_id  BIGINT NOT NULL DEFAULT 0,
        made_at     BIGINT NOT NULL,
        alt         TEXT NOT NULL CHECK (char_length(alt) BETWEEN 1 AND 400),
        layout      TEXT NOT NULL CHECK (char_length(layout) <= 2500),
        places      JSONB NOT NULL CHECK (jsonb_typeof(places) = 'array'),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX map_notes_campaign ON map_notes (campaign_id, id);
    CREATE INDEX map_notes_person ON map_notes (guild_id, user_id);
    CREATE INDEX map_notes_message ON map_notes (guild_id, message_id) WHERE message_id <> 0;
    """ + _isolate("map_notes")

HANDOVER_EMAIL = """
    -- Hand-over by email (#2532, docs/PLAN.md, "Hand-over by email"): an offer is no longer made to
    -- a Discord account but sent to an email address, and the person who opens its one-use link
    -- on the website and accepts becomes the new owner. The address is never stored here (it goes
    -- in the email and nowhere else): the offer holds only a SHA-256 of the link's token. Until
    -- the link is used `to_user_id` is 0, which matches nobody; the website's claim sets it to
    -- the signed-in person, and the rest is the offer machinery of 0021 (one open offer per
    -- campaign, seven days, the owner told when it ends). A new column on an existing table: no
    -- rows change, so there is no backfill policy.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN via_email  BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN token_hash TEXT CHECK (token_hash IS NULL OR char_length(token_hash) = 64);
    CREATE UNIQUE INDEX campaign_handover_offers_token
        ON campaign_handover_offers (token_hash) WHERE token_hash IS NOT NULL;

    -- How many offers by email this person has sent in the last day, for the limit of five a
    -- day, counted over all their campaigns and servers (the offers table is scoped to one
    -- server at a time, so the count reads the mail log, which is everyone's). The caller must
    -- be that person, or a job with no person set.
    CREATE FUNCTION dmbot_handover_emails_since(p_user BIGINT, p_since BIGINT) RETURNS INTEGER
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_user() IS NOT NULL AND dmbot_current_user() <> p_user THEN
            RETURN 0;
        END IF;
        RETURN (SELECT count(*)::INTEGER FROM sent_emails e
                WHERE e.user_id = p_user AND e.kind LIKE 'handover:%' AND e.sent_at >= p_since);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_emails_since(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_emails_since(BIGINT, BIGINT) FROM PUBLIC;
"""

HANDOVER_CLAIM = """
    -- Opening a hand-over link on the website (#2532, slice B). The link names the server and the
    -- offer and carries a secret whose SHA-256 the offer holds, so the door can read the one offer
    -- in the one server (every server table is scoped by server) and nothing else. It answers only
    -- for a signed-in person, and only for an offer that is still open, by email, unexpired, not
    -- yet claimed by someone else, and whose campaign still belongs to the person who offered it.
    -- Anything else is NULL, the same whatever the reason, so a link says nothing about why it
    -- ended. With p_claim the offer becomes this person's (their id and name are written once; the
    -- link then works for no one else), unless they are the owner. The rest of the answer (plan,
    -- slot, ownership) is the campaign store's own accept, with the person's session as always.
    -- The caller connects scoped to the server `g` (the door refuses any other).
    CREATE FUNCTION dmbot_handover_open(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_claim BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        r RECORD;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        IF p_claim THEN  -- two claims at once: one waits for the other, then sees it taken
            PERFORM 1 FROM campaign_handover_offers WHERE guild_id = g AND id = o FOR UPDATE;
        END IF;
        SELECT f.campaign_id, f.from_user_id, f.from_name, f.to_user_id, f.created_at,
               c.name AS campaign_name
            INTO r
            FROM campaign_handover_offers f
            JOIN campaigns c ON c.guild_id = f.guild_id AND c.id = f.campaign_id
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me)
              AND c.owner_user_id = f.from_user_id;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF p_claim AND r.from_user_id <> me AND r.to_user_id = 0 THEN
            UPDATE campaign_handover_offers
                SET to_user_id = me, to_name = left(coalesce(p_name, ''), 80)
                WHERE guild_id = g AND id = o;
        END IF;
        RETURN jsonb_build_object(
            'campaign_id', r.campaign_id, 'campaign_name', r.campaign_name,
            'from_user_id', r.from_user_id, 'from_name', r.from_name,
            'expires_at', r.created_at + 604800);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;

    -- Gives a claim back (#2532): an accept that fails after the link was claimed (no room any
    -- more, the campaign changed hands) must not leave the link dead for the person it was for.
    -- Only the person who holds the claim, only on an offer still open and still unanswered.
    CREATE FUNCTION dmbot_handover_release(g BIGINT, o BIGINT, p_hash TEXT) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN;
        END IF;
        UPDATE campaign_handover_offers SET to_user_id = 0, to_name = ''
            WHERE guild_id = g AND id = o AND via_email AND token_hash = p_hash
              AND status = 'open' AND to_user_id = me;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_release(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_release(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- "No thanks" from whoever holds the link, wherever they are (#2532): the website's role can
    -- write an answer only in the servers of its session, and the person who was emailed is often
    -- not in the campaign's server. Same checks as dmbot_handover_open; the offer is declined
    -- under their id and name, and the bot is told (as for every answer made on the website) so
    -- the owner hears. NULL, the same as any ended link; the owner cannot decline their own.
    CREATE FUNCTION dmbot_handover_decline(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN false;
        END IF;
        UPDATE campaign_handover_offers f
            SET status = 'declined', decided_at = dmbot_now(), to_user_id = me,
                to_name = left(coalesce(p_name, ''), 80)
            FROM campaigns c
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me) AND f.from_user_id <> me
              AND c.guild_id = f.guild_id AND c.id = f.campaign_id
              AND c.owner_user_id = f.from_user_id;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed = 1 THEN
            PERFORM pg_notify('dmbot_handover_decided', g::text || ':' || o::text);
        END IF;
        RETURN changed = 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

HANDOVER_MOVE = """
    -- A campaign handed over by email to a person who wants it in another server (#2532, slice C;
    -- docs/PLAN.md, "Hand-over by email", point 7). The website cannot copy a campaign between
    -- servers (it must never read or write another server's tables), so it only records the wish
    -- here: `move_to_guild_id`. DMbot's bot then moves the campaign: one update of its row, which
    -- migration 0215 lets every table follow. (`moved_campaign_id` was for an earlier design that
    -- copied the campaign; nothing writes it.) While a move is asked for, the offer stays 'open'
    -- but cannot be answered, taken back or run out. Two new columns on an existing table: no
    -- rows change.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN move_to_guild_id BIGINT
            CHECK (move_to_guild_id IS NULL OR move_to_guild_id > 0),
        ADD COLUMN moved_campaign_id TEXT;

    -- Claims the link for this person and asks for the move, in one step (#2532). Same rules as
    -- dmbot_handover_open with a claim, and the target must be one of the session's own servers
    -- with DMbot installed. NULL, whatever the reason, the same as any ended link.
    CREATE FUNCTION dmbot_handover_request_move(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_target BIGINT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ready BOOLEAN;
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_target IS NULL OR p_target = g
           OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN
            RETURN false;
        END IF;
        IF NOT (p_target = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN false;
        END IF;
        PERFORM set_config('dmbot.guild_id', p_target::text, true);
        SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = p_target AND left_at IS NULL)
            INTO ready;
        IF NOT ready THEN
            RETURN false;
        END IF;
        PERFORM set_config('dmbot.guild_id', g::text, true);
        UPDATE campaign_handover_offers f
            SET to_user_id = me, to_name = left(coalesce(p_name, ''), 80),
                move_to_guild_id = p_target
            FROM campaigns c
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me) AND f.from_user_id <> me
              AND f.move_to_guild_id IS NULL
              AND c.guild_id = f.guild_id AND c.id = f.campaign_id
              AND c.owner_user_id = f.from_user_id;
        GET DIAGNOSTICS changed = ROW_COUNT;
        RETURN changed = 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)
        FROM PUBLIC;
"""


# A pending move makes the doors of 0213 look away (#2532, slice C): the claimant's look says the
# link has ended, a release leaves the claim alone, and nobody can decline underneath the move.
# Built from 0213's own text, so the two cannot drift: the same doors, the same grants and pinned
# search_path, with one more condition each.
_MOVE_BLIND = (
    HANDOVER_CLAIM.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    .replace(
        "AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()",
        "AND f.status = 'open' AND f.move_to_guild_id IS NULL"
        " AND f.created_at + 604800 > dmbot_now()",
    )
    .replace(
        "AND status = 'open' AND to_user_id = me;",
        "AND status = 'open' AND move_to_guild_id IS NULL AND to_user_id = me;",
    )
)
assert _MOVE_BLIND.count("move_to_guild_id IS NULL") == 3, "0214 must change all three doors"
HANDOVER_MOVE += _MOVE_BLIND


RECAP_STORY_ROOM = """
    -- The recap story's column limit goes from 6,000 to 30,000 characters (#2601): a length is a
    -- target in the writer's instructions and never a reason to cut what it wrote, so the column is
    -- only a guard (`fit_summary` shortens past it and logs). The number is
    -- `recaps.fixed_lines.STORY_COLUMN_MAX`; a test keeps the two equal. The constraint is
    -- replaced, no row is rewritten, so no row-level security is touched.
    ALTER TABLE recap_jobs
        DROP CONSTRAINT recap_jobs_summary_check,
        DROP CONSTRAINT recap_jobs_original_check,
        ADD CONSTRAINT recap_jobs_summary_check CHECK (char_length(summary) <= 30000),
        ADD CONSTRAINT recap_jobs_original_check CHECK (char_length(original) <= 30000);
"""


WEB_OWNED_CAMPAIGNS = """
    -- The website's two doors for a campaign's owner, in any server (#2554, docs/PLAN.md,
    -- "Everything on the website except picking Discord channels"). The website's role sees a
    -- server only when it is in the session's own list (`web_session_servers`, not widened here):
    -- an owner whose server has gone, or whose bot was removed, could not see or delete their own
    -- campaign. These doors are keyed by the signed-in account and return only list fields, or do
    -- the one delete. New functions only: no rows change, so no row-level-security policy is
    -- needed.

    -- The signed-in account's own campaigns in every server DMbot knows. `owner_campaigns` has the
    -- ids but deliberately no server, so each server of the routing table is looked in, with
    -- that server's scope, for those ids only.
    CREATE FUNCTION dmbot_web_owned_campaigns() RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ids TEXT[];
        g BIGINT;
        found JSONB;
        found_all JSONB := '[]'::jsonb;
        saved TEXT := coalesce(current_setting('dmbot.guild_id', true), '');
    BEGIN
        IF me IS NULL THEN
            RETURN found_all;
        END IF;
        SELECT coalesce(array_agg(campaign_id), '{}') INTO ids
            FROM owner_campaigns WHERE owner_user_id = me;
        IF cardinality(ids) = 0 THEN
            RETURN found_all;
        END IF;
        FOR g IN SELECT guild_id FROM campaign_guilds ORDER BY guild_id LOOP
            PERFORM set_config('dmbot.guild_id', g::text, true);
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'id', c.id, 'guildId', c.guild_id::text, 'name', c.name,
                       'lastPlayedAt', c.last_played_at) ORDER BY c.name), '[]'::jsonb)
                INTO found
                FROM campaigns c
                WHERE c.guild_id = g AND c.id = ANY (ids) AND c.owner_user_id = me;
            found_all := found_all || found;
        END LOOP;
        PERFORM set_config('dmbot.guild_id', saved, true);
        RETURN found_all;
    END
    $fn$;

    -- Delete a campaign from the website: the same rule as Discord's "Delete this campaign"
    -- (#1608, #2556). The owner may, in any server; with no owner, any game master who can reach
    -- the server may. The typed name must match (case and spacing ignored), and not while a
    -- session runs. The answer says which of these stopped it; 'not_found' for a campaign that is
    -- not this person's to know about. The caller connects scoped to the campaign's server, like
    -- every door.
    CREATE FUNCTION dmbot_web_delete_campaign(g BIGINT, c TEXT, typed TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        camp campaigns%ROWTYPE;
        is_dm BOOLEAN;
        had_channels BOOLEAN;
    BEGIN
        IF me IS NULL OR g IS NULL OR c IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) INTO is_dm;
        IF camp.owner_user_id IS DISTINCT FROM me THEN
            -- Not the owner. Someone who is not a game master of it, or cannot reach its server,
            -- is told nothing about it; a game master is told it is not theirs to delete.
            IF NOT is_dm OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            IF camp.owner_user_id IS NOT NULL THEN
                RETURN jsonb_build_object('status', 'not_the_owner');
            END IF;
        END IF;
        -- The typed name arrives as its `name_key` (Python's, the one Discord's delete uses), so
        -- there is one rule for what counts as the same name.
        IF typed IS DISTINCT FROM camp.name_key THEN
            RETURN jsonb_build_object('status', 'name_mismatch');
        END IF;
        IF EXISTS (SELECT 1 FROM active_sessions a WHERE a.guild_id = g AND a.campaign_id = c) THEN
            RETURN jsonb_build_object('status', 'session_running');
        END IF;
        had_channels := camp.dm_screen_channel_id IS NOT NULL
            OR camp.transcript_channel_id IS NOT NULL OR camp.gametime_channel_id IS NOT NULL
            OR camp.dmbchat_channel_id IS NOT NULL OR camp.dm_bot_channel_id IS NOT NULL
            OR camp.rules_channel_id IS NOT NULL OR camp.npcs_channel_id IS NOT NULL;
        -- Every table of the campaign goes with its row: each one's foreign key cascades, as
        -- for Discord's delete.
        DELETE FROM campaigns WHERE guild_id = g AND id = c;
        RETURN jsonb_build_object('status', 'deleted', 'channels', had_channels);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_owned_campaigns() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

WEB_DELETE_NOTICES = (
    """
    -- What the bot needs to finish a campaign the website deleted (#2554, part 1b): tidy the
    -- Discord channels the way Discord's own delete does, and tell the other game masters. The
    -- website cannot do either, and the campaign's row (with its channel ids) is gone when the bot
    -- hears of it, so the delete door leaves this note first. It holds the campaign's name, who
    -- deleted it, the other game masters' IDs and the channel IDs: nothing of the story. The bot
    -- takes it (deleting the row) within seconds; one it never took is swept after a week. Isolated
    -- per server, never read by the website's role, and left out of backups: it is not campaign
    -- data, and has no key to the campaign (which no longer exists).
    CREATE TABLE web_delete_notices (
        guild_id             BIGINT NOT NULL,
        id                   TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        gone_id              TEXT NOT NULL,
        campaign_name        TEXT NOT NULL CHECK (char_length(campaign_name) <= 200),
        by_user_id           BIGINT NOT NULL CHECK (by_user_id > 0),
        other_dms            BIGINT[] NOT NULL DEFAULT '{}',
        dm_screen_channel_id    BIGINT,
        transcript_channel_id   BIGINT,
        gametime_channel_id     BIGINT,
        dmbchat_channel_id      BIGINT,
        dm_bot_channel_id       BIGINT,
        rules_channel_id        BIGINT,
        npcs_channel_id         BIGINT,
        created_at           BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_delete_notices_waiting ON web_delete_notices (guild_id, created_at);
    """
    + _isolate("web_delete_notices")
    + r"""
    -- The website's delete door again (replaces 0216's): the same checks and the same delete, and
    -- first the note above. The answer is unchanged.
    CREATE OR REPLACE FUNCTION dmbot_web_delete_campaign(g BIGINT, c TEXT, typed TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        camp campaigns%ROWTYPE;
        is_dm BOOLEAN;
        had_channels BOOLEAN;
    BEGIN
        IF me IS NULL OR g IS NULL OR c IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) INTO is_dm;
        IF camp.owner_user_id IS DISTINCT FROM me THEN
            IF NOT is_dm OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            IF camp.owner_user_id IS NOT NULL THEN
                RETURN jsonb_build_object('status', 'not_the_owner');
            END IF;
        END IF;
        IF typed IS DISTINCT FROM camp.name_key THEN
            RETURN jsonb_build_object('status', 'name_mismatch');
        END IF;
        IF EXISTS (SELECT 1 FROM active_sessions a WHERE a.guild_id = g AND a.campaign_id = c) THEN
            RETURN jsonb_build_object('status', 'session_running');
        END IF;
        had_channels := camp.dm_screen_channel_id IS NOT NULL
            OR camp.transcript_channel_id IS NOT NULL OR camp.gametime_channel_id IS NOT NULL
            OR camp.dmbchat_channel_id IS NOT NULL OR camp.dm_bot_channel_id IS NOT NULL
            OR camp.rules_channel_id IS NOT NULL OR camp.npcs_channel_id IS NOT NULL;
        INSERT INTO web_delete_notices (
            guild_id, id, gone_id, campaign_name, by_user_id, other_dms,
            dm_screen_channel_id, transcript_channel_id, gametime_channel_id,
            dmbchat_channel_id, dm_bot_channel_id, rules_channel_id, npcs_channel_id, created_at)
        SELECT g, replace(gen_random_uuid()::TEXT, '-', ''), c, left(camp.name, 200), me,
               COALESCE((SELECT array_agg(d.user_id ORDER BY d.user_id) FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id <> me), '{}'),
               camp.dm_screen_channel_id, camp.transcript_channel_id, camp.gametime_channel_id,
               camp.dmbchat_channel_id, camp.dm_bot_channel_id, camp.rules_channel_id,
               camp.npcs_channel_id, floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        -- The server stays on the routing list until the bot has taken the note, so it is looked
        -- at even if this was its last campaign.
        INSERT INTO campaign_guilds (guild_id) VALUES (g) ON CONFLICT DO NOTHING;
        DELETE FROM campaigns WHERE guild_id = g AND id = c;
        RETURN jsonb_build_object('status', 'deleted', 'channels', had_channels);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)

NPC_OR_PC = """
    -- Every character is an NPC or a PC (owner, 2026-10-23, #2562): the role "god" goes. A god
    -- becomes an NPC and keeps the word "god" in its description (unless it already says so),
    -- on the entry and in the change log's snapshots, so Undo across this still puts back an
    -- NPC. Nothing is deleted. The statements read and write memory_entities and memory_changes,
    -- which have forced row-level security and are migrated unscoped, so each is opened for this
    -- one migration only (CLAUDE.md, "A migration that changes rows must open row-level security
    -- for itself").
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_entities SET
        role = 'npc',
        description = CASE
            WHEN description ~* '\\m(god|goddess|deity)\\M' THEN description
            ELSE btrim(left(description, 300 - 7) || ' A god.') END
        WHERE role = 'god';
    UPDATE memory_changes SET
        before = CASE WHEN before->>'role' = 'god' THEN jsonb_set(
            jsonb_set(before, '{role}', '"npc"'),
            '{description}', to_jsonb(CASE
                WHEN coalesce(before->>'description', '') ~* '\\m(god|goddess|deity)\\M'
                THEN coalesce(before->>'description', '')
                ELSE btrim(left(coalesce(before->>'description', ''), 300 - 7) || ' A god.') END))
            ELSE before END,
        after = CASE WHEN after->>'role' = 'god' THEN jsonb_set(
            jsonb_set(after, '{role}', '"npc"'),
            '{description}', to_jsonb(CASE
                WHEN coalesce(after->>'description', '') ~* '\\m(god|goddess|deity)\\M'
                THEN coalesce(after->>'description', '')
                ELSE btrim(left(coalesce(after->>'description', ''), 300 - 7) || ' A god.') END))
            ELSE after END
        WHERE table_name = 'memory_entities'
          AND (before->>'role' = 'god' OR after->>'role' = 'god');
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_entities;
    DO $do$
    DECLARE c TEXT;
    BEGIN
        FOR c IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'memory_entities'::regclass AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%god%' LOOP
            EXECUTE format('ALTER TABLE memory_entities DROP CONSTRAINT %I', c);
        END LOOP;
    END
    $do$;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_role_check
        CHECK (role IS NULL OR role IN ('player_character', 'npc'));
"""

NAME_PASS = """
    -- PlotBot's name pass on a finished session's cleaned transcript (#2628): when it was done
    -- (Unix seconds), 0 when it has not been. The recap of a session waits for it (a bounded wait,
    -- in code). Every session that exists now is marked done, so nothing old is asked again and no
    -- old recap waits. A time only, no words.
    ALTER TABLE transcript_sessions ADD COLUMN names_repaired_at BIGINT NOT NULL DEFAULT 0;
    -- Migrations run with no server set, so row-level security hides every row: open the table to
    -- this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON transcript_sessions
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE transcript_sessions SET names_repaired_at = GREATEST(COALESCE(ended_at, started_at), 1);
    DROP POLICY migrate_backfill ON transcript_sessions;
"""

IMPORT_STEPS = """
    -- How far each imported session's cleaning has got and when anything about it last moved
    -- (#2564), so the website can show "420 of 900 lines" and say "stalled" when nothing has moved
    -- for longer than the stage's limit (kept in code, `progress.py`). Counts and times only. The
    -- columns have defaults, so no row changes and no backfill policy is needed. A trigger stamps
    -- `updated_at` whenever a step column changes, so no worker has to remember to.
    ALTER TABLE session_imports
        ADD COLUMN step_done  INT NOT NULL DEFAULT 0,
        ADD COLUMN step_total INT NOT NULL DEFAULT 0,
        ADD COLUMN updated_at BIGINT NOT NULL DEFAULT 0;

    CREATE FUNCTION session_imports_moved() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
    BEGIN
        IF NEW.updated_at = OLD.updated_at AND (
               NEW.state, NEW.scanned, NEW.scan_finished, NEW.scan_tries, NEW.recap_started,
               NEW.recap_tries, NEW.step_done, NEW.step_total, NEW.scan_gave_up)
           IS DISTINCT FROM (
               OLD.state, OLD.scanned, OLD.scan_finished, OLD.scan_tries, OLD.recap_started,
               OLD.recap_tries, OLD.step_done, OLD.step_total, OLD.scan_gave_up) THEN
            NEW.updated_at := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER session_imports_moved BEFORE UPDATE ON session_imports
        FOR EACH ROW EXECUTE FUNCTION session_imports_moved();

    -- dmbot_import_progress, again (replaces 0154's): the same per-session rows with the counts and
    -- times the shared progress shape needs. NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_progress(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', i.file_name, 'state', i.state, 'reason', i.reason,
                       'date', t.started_at,
                       'namesUnchecked', i.scan_gave_up,
                       'namesToAnswer', CASE WHEN h.asked THEN h.names_open ELSE 0 END,
                       'imported', i.imported_at,
                       'moved', i.updated_at,
                       'recapMoved', COALESCE(r.updated_at, 0),
                       'stepDone', i.step_done, 'stepTotal', i.step_total,
                       'scanned', i.scanned, 'recapState', r.state,
                       'stage', CASE
                           WHEN i.state = 'failed' THEN 'failed'
                           WHEN i.state <> 'done' THEN 'cleaning'
                           WHEN NOT i.scan_finished THEN 'checking_names'
                           WHEN NOT i.write_recap THEN 'done'
                           WHEN h.session_at IS NOT NULL AND NOT h.asked THEN 'finding_names'
                           WHEN h.session_at IS NOT NULL THEN 'waiting_for_names'
                           WHEN r.state = 'ready' THEN 'recap_ready'
                           WHEN r.state = 'failed' THEN 'recap_failed'
                           WHEN r.session_at IS NOT NULL THEN 'writing_recap'
                           WHEN i.recap_started THEN 'recap_not_written'
                           ELSE 'starting_recap' END)
                   ORDER BY t.started_at, i.session_id)
            FROM session_imports i
            JOIN transcript_sessions t ON t.guild_id = i.guild_id AND t.id = i.session_id
            LEFT JOIN recap_holds h ON h.guild_id = i.guild_id AND h.campaign_id = i.campaign_id
                AND h.session_at = t.started_at
            LEFT JOIN recap_jobs r ON r.guild_id = i.guild_id AND r.campaign_id = i.campaign_id
                AND r.session_at = t.started_at
            WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""


# A campaign moves to another server by changing `guild_id` on its `campaigns` row (#2532, slice
# C2); every foreign key that carries `guild_id` must follow it, or the move would be refused for
# the first child row or leave rows behind. Rebuilt from the catalog so no table is forgotten
# (tests/test_campaign_move_cascade.py checks it still holds). Dropping a key takes an exclusive
# lock on its table, and the migration is one transaction, so those locks last until it ends: deploy
# it with the bot stopped (docs/testing-status.log). Each key is added NOT VALID and then validated.
# Every foreign key that carries `guild_id` becomes ON UPDATE CASCADE (found from the catalog, so
# no table is forgotten). Run by 0215 and again by 0234: a table made between them (0225's
# `recap_upload_rights`) was missed the first time. Already-cascading keys are left alone.
_CASCADE_ALL_KEYS = """
    DO $$
    DECLARE r record;
    BEGIN
        FOR r IN
            -- ON UPDATE has to come before DEFERRABLE, so it is put in ahead of it.
            SELECT c.conrelid::regclass AS tbl, c.conname,
                   regexp_replace(pg_get_constraintdef(c.oid),
                                  '( (NOT )?DEFERRABLE.*)?$', ' ON UPDATE CASCADE\\1') AS def
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 AND a.attname = 'guild_id'
            WHERE c.contype = 'f' AND n.nspname = current_schema() AND c.confupdtype = 'a'
        LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I %s NOT VALID',
                r.tbl, r.conname, r.def);
            EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', r.tbl, r.conname);
        END LOOP;
    END $$;
"""

# The same catalog loop again, for the tables made after 0215 (0225's `recap_upload_rights`); a
# table made later than this declares ON UPDATE CASCADE itself (CLAUDE.md, #2550).
MOVE_CASCADE_LATE = _CASCADE_ALL_KEYS

CAMPAIGN_MOVE_CASCADE = (
    _CASCADE_ALL_KEYS
    + """

    -- The bot's lease on a move that is under way, and the door that asks for a move, made again
    -- (0214 is the same as it was when it first shipped): it now also checks that the caller is
    -- scoped to the campaign's server, goes back to that scope when the target has no DMbot,
    -- and wakes the bot. A new column and a function: no rows change.
    ALTER TABLE campaign_handover_offers ADD COLUMN move_claimed_at BIGINT;
    CREATE OR REPLACE FUNCTION dmbot_handover_request_move(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_target BIGINT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ready BOOLEAN;
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_target IS NULL OR p_target = g
           OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN false;  -- (the caller connects scoped to the campaign's server, like every door)
        END IF;
        IF NOT (p_target = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN false;
        END IF;
        -- Whether DMbot is installed in the target is a fact of the TARGET server, whose rows are
        -- visible only when scoped to it: so the door looks there for this one read and comes
        -- straight back. It reads nothing else there.
        PERFORM set_config('dmbot.guild_id', p_target::text, true);
        SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = p_target AND left_at IS NULL)
            INTO ready;
        IF NOT ready THEN
            PERFORM set_config('dmbot.guild_id', g::text, true);  -- back where the caller was
            RETURN false;
        END IF;
        PERFORM set_config('dmbot.guild_id', g::text, true);
        UPDATE campaign_handover_offers f
            SET to_user_id = me, to_name = left(coalesce(p_name, ''), 80),
                move_to_guild_id = p_target
            FROM campaigns c
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me) AND f.from_user_id <> me
              AND f.move_to_guild_id IS NULL
              AND c.guild_id = f.guild_id AND c.id = f.campaign_id
              AND c.owner_user_id = f.from_user_id;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed = 1 THEN  -- wakes the bot, which does the move (a sweep catches a missed one)
            PERFORM pg_notify('dmbot_handover_move', g::text || ':' || o::text);
        END IF;
        RETURN changed = 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- The one write that may take a campaign out of the server in scope: the move sets both
    -- settings in its own transaction (campaigns/moves.py), and the policy then lets that one
    -- campaign be seen and written in the server it goes to. (It is for all commands because an
    -- update's new row must also be readable, which a policy for updates alone does not give.)
    -- Policies add up, so with the settings unset every other write stays as it was.
    -- Only for the role the bot connects as (the one that ran this migration), as `move_all` is:
    -- the website's role never gets it.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY campaign_move ON campaigns TO %I'
            ' USING ((guild_id = dmbot_current_guild()'
            '         OR guild_id = NULLIF(current_setting(''dmbot.moving_to'', true),'
            ' '''')::BIGINT)'
            '        AND id = NULLIF(current_setting(''dmbot.moving_campaign'', true), ''''))'
            ' WITH CHECK (guild_id = NULLIF(current_setting(''dmbot.moving_to'', true),'
            ' '''')::BIGINT'
            '             AND id = NULLIF(current_setting(''dmbot.moving_campaign'', true), ''''))',
            current_user);
    END
    $do$;

    -- Whether the campaign an emailed link is for shares a rule set: such a campaign cannot move to
    -- another server yet (#2551), and the page says so before anyone accepts or buys a plan. Only
    -- the link's own secret opens the question.
    CREATE FUNCTION dmbot_handover_shares_rules(g BIGINT, o BIGINT, p_hash TEXT) RETURNS BOOLEAN
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT g IS NOT NULL AND o IS NOT NULL AND p_hash ~ '^[0-9a-f]{64}$'
            AND current_setting('dmbot.guild_id', true) IS NOT DISTINCT FROM g::text
            AND EXISTS (
                SELECT 1 FROM campaign_handover_offers f
                JOIN rule_sets s ON s.guild_id = f.guild_id AND s.campaign_id = f.campaign_id
                WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
                  AND f.status = 'open' AND s.status = 'shared')
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- Three more facts about a hand-over that moves: when the move was asked for (an offer whose
    -- move never ran is opened again after an hour), and, when a move did not happen, who had
    -- accepted and the plain reason, which only that person is shown (the owner is told only that
    -- it did not happen, because the reason can be about the other person's plan).
    ALTER TABLE campaign_handover_offers
        ADD COLUMN move_asked_at BIGINT,
        ADD COLUMN move_failed_for BIGINT,
        ADD COLUMN move_failed_reason TEXT;
    CREATE FUNCTION dmbot_note_move_asked() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.move_to_guild_id IS NOT NULL AND OLD.move_to_guild_id IS NULL THEN
            NEW.move_asked_at := dmbot_now();
            NEW.move_failed_for := NULL;
            NEW.move_failed_reason := NULL;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER campaign_handover_offers_move_asked
        BEFORE UPDATE OF move_to_guild_id ON campaign_handover_offers
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_move_asked();

    -- The plain reason a move did not happen, for the person who had accepted and nobody else
    -- (the owner is told only that it did not happen). Shown on that person's page.
    CREATE FUNCTION dmbot_handover_move_failed(g BIGINT, o BIGINT, p_hash TEXT) RETURNS TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT f.move_failed_reason FROM campaign_handover_offers f
        WHERE dmbot_current_user() IS NOT NULL AND f.guild_id = g AND f.id = o AND f.via_email
          AND f.token_hash = p_hash AND f.status = 'open'
          AND f.move_failed_for = dmbot_current_user()
          AND current_setting('dmbot.guild_id', true) IS NOT DISTINCT FROM g::text
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_move_failed(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_move_failed(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- The server it arrives in is one that holds campaigns now (the retention job's routing list).
    CREATE TRIGGER campaign_guilds_note_move
        AFTER UPDATE OF guild_id ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_campaign_guild();
"""
)

WEB_BACKUPS = (
    """
    -- The copy of a campaign the website downloads (#2554, part 1b). The website's role cannot read
    -- the tables a backup holds, so it asks for a copy here and the bot builds it the way
    -- Discord's Download does (same sections, same packing), from the database alone, so a server
    -- DMbot cannot reach works too. One row is one request: waiting, then the bot reads and packs
    -- (`stage`, with `updated_at` for the shared progress shape of #2564), then the file waits in
    -- `file` (at most 25 MB, what a restore takes) for the person who asked. The row goes when the
    -- file is fetched, or 15 minutes after the request, whichever is first; the bot sweeps the
    -- rest. Only the person who asked can read or fetch it. A copy holds the campaign's secrets,
    -- like any backup, so it is never logged and never kept beyond that; the row is gone with its
    -- campaign, isolated per server, and not itself in a backup.
    CREATE TABLE web_backups (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550): a campaign that changes
        -- server or id carries its waiting copies with it.
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'packing', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        file        BYTEA,
        file_name   TEXT NOT NULL DEFAULT '' CHECK (char_length(file_name) <= 200),
        size        INT NOT NULL DEFAULT 0,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_backups_open ON web_backups (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_backups")
    + r"""
    -- Ask for a copy. The owner may in any server (the server need not be on the session's list,
    -- as for the delete); a game master of the campaign may in a server the session can reach.
    -- Anyone else gets NULL. The plan (copies are for a paid plan, none for Try It) is judged by
    -- the bot, which owns the plan check, and it says so in the row. A person who already has a
    -- copy on the way gets that one back; at most 5 requests in 15 minutes (a fetched copy
    -- keeps its row, without the file, until then, so fetching does not reset the count).
    CREATE FUNCTION dmbot_web_backup_add(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        camp campaigns%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL OR g IS NULL OR c IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF camp.owner_user_id IS DISTINCT FROM me THEN
            IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                           WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me)
               OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN NULL;
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('web_backup:' || g::TEXT || c || me, 0));
        DELETE FROM web_backups b WHERE b.guild_id = g AND b.created_at < now_s - 900;
        SELECT b.id INTO have FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.user_id = me
              AND (b.state IN ('waiting', 'working') OR (b.state = 'done' AND b.file IS NOT NULL))
            ORDER BY b.created_at DESC LIMIT 1;
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_backups b WHERE b.guild_id = g AND b.campaign_id = c
            AND b.user_id = me) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_backups (guild_id, campaign_id, id, user_id, created_at, updated_at)
            VALUES (g, c, rid, me, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one copy stands, for the person who asked (nobody else): the stage and times for the
    -- shared progress shape, the plain error word, and the file's name and size once it is ready.
    CREATE FUNCTION dmbot_web_backup_read(g BIGINT, c TEXT, bid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- Named columns, so a poll never reads the file itself.
        SELECT b.state, b.stage, b.error, b.file_name, b.size, b.created_at, b.updated_at,
               (b.file IS NOT NULL) AS has_file INTO r
            FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user()
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state = 'done' AND NOT r.has_file THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'fileName', r.file_name, 'size', r.size, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;

    -- The file, once, for the person who asked: it is emptied from the row as it is read. NULL when
    -- it is not theirs, not ready or already taken.
    CREATE FUNCTION dmbot_web_backup_take(g BIGINT, c TEXT, bid TEXT) RETURNS BYTEA
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out BYTEA;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- The file leaves the row as it is read; the row stays (empty) for the request count.
        SELECT b.file INTO out FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user() AND b.state = 'done' AND b.file IS NOT NULL
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900
            FOR UPDATE;
        IF out IS NULL THEN
            RETURN NULL;
        END IF;
        UPDATE web_backups b SET file = NULL, size = 0
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_take(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_backup_add(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_backup_take(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

OPTIONAL_RULES_READ = r"""
    -- A campaign's optional-rule choices for the website's Settings tab (#2753): the main rules it
    -- plays by, the campaign-wide default, and the choices the DM made rule by rule. Read only, DM
    -- of the campaign only (as the other Settings reads), and nothing else leaves it; the API joins
    -- it with the rules catalog. NULL for anyone else.
    CREATE FUNCTION dmbot_web_optional_rules(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'target', camp.target_ruleset,
            'default', camp.optional_rules_default,
            'overrides', COALESCE((
                SELECT jsonb_object_agg(o.rule_id, o.enabled)
                FROM campaign_optional_rules o
                WHERE o.guild_id = g AND o.campaign_id = c), '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_optional_rules(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_optional_rules(BIGINT, TEXT) FROM PUBLIC;
"""

OPTIONAL_RULE_SETTING = r"""
    -- A fourth choice on the website's Settings queue (#2753): an optional rule switched on or off.
    -- The queue already carries the three settings (0197); here `setting` also accepts
    -- 'optional_rule', `value` may be the rule's id (the longest is 18 characters), and a new
    -- `turn_on` column says which way. `turn_on` is set exactly for an optional rule and is NULL
    -- for every other setting (so it never shares a meaning with `flag`, the DM's yes to a shorter
    -- transcript limit). No row changes: no backfill policy is needed.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_value_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule')),
        ADD CONSTRAINT setting_actions_value_check CHECK (char_length(value) <= 40),
        ADD COLUMN turn_on BOOLEAN,
        ADD CONSTRAINT setting_actions_turn_on_check
            CHECK ((setting = 'optional_rule') = (turn_on IS NOT NULL));

    -- Queue one optional-rule choice. Same guard as `dmbot_web_setting_add`: the caller is a DM of
    -- the campaign in a server on the session's list, and the campaign's queue is not full. The
    -- API has already checked the id against the catalog (a rule list that lives in Python); the
    -- bot checks again, with the campaign's main rules, when it applies it. 'ok' with the id,
    -- 'bad_value' for a malformed id or no on/off, 'too_soon'; NULL for anyone not a DM.
    CREATE FUNCTION dmbot_web_optional_rule_add(g BIGINT, c TEXT, rid TEXT, on_ BOOLEAN)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF rid IS NULL OR rid !~ '^[a-z0-9-]{1,40}$' OR on_ IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, turn_on,
                                     created_at)
            VALUES (g, c, qid, dmbot_current_user(), 'optional_rule', rid, on_, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', qid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""


WEB_BACKUP_PARTS = (
    """
    -- The transcripts files of a copy the website downloads (#2772, #2552): Discord's backup
    -- sends a campaign's transcripts as extra files next to the main one, and a website copy is
    -- the same set. One row is one such file, waiting in the database for the person who asked
    -- (like `web_backups.file`: kept 15 minutes at most, handed over once, never logged, the DM's
    -- secrets inside no more than in any backup). `web_backups.parts` says how many there were.
    -- The row goes with its copy; `data` is emptied as the file is fetched.
    ALTER TABLE web_backups ADD COLUMN parts INT NOT NULL DEFAULT 0;
    CREATE TABLE web_backup_parts (
        guild_id  BIGINT NOT NULL,
        backup_id TEXT NOT NULL,
        FOREIGN KEY (guild_id, backup_id)
            REFERENCES web_backups (guild_id, id) ON DELETE CASCADE ON UPDATE CASCADE,
        n         INT NOT NULL CHECK (n >= 1 AND n <= 24),
        data      BYTEA,
        size      INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, backup_id, n)
    );
    """
    + _isolate("web_backup_parts")
    + r"""
    -- (Asking for a copy is 0219's door, unchanged: a copy whose main file has been taken is never
    -- handed back, so a retry after a failed part starts a fresh copy and the old one's leftovers
    -- go with the 15-minute sweep, #2778.)

    -- Where one copy stands: the same answer as before, plus the files still waiting for the
    -- person (number and size, never the bytes).
    CREATE OR REPLACE FUNCTION dmbot_web_backup_read(g BIGINT, c TEXT, bid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
        waiting JSONB;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT b.state, b.stage, b.error, b.file_name, b.size, b.parts, b.created_at,
               b.updated_at, (b.file IS NOT NULL) AS has_file INTO r
            FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user()
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('n', p.n, 'size', p.size) ORDER BY p.n),
                        '[]'::jsonb) INTO waiting
            FROM web_backup_parts p
            WHERE p.guild_id = g AND p.backup_id = bid AND p.data IS NOT NULL;
        -- Gone once the main file and every part have been fetched.
        IF r.state = 'done' AND NOT r.has_file AND waiting = '[]'::jsonb THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'fileName', r.file_name, 'size', r.size, 'fileWaiting', r.has_file,
            'parts', waiting, 'partsTotal', r.parts,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;

    -- One transcripts file, once, for the person who asked: emptied from the row as it is read.
    -- NULL when it is not theirs, not ready or already taken.
    CREATE FUNCTION dmbot_web_backup_take_part(g BIGINT, c TEXT, bid TEXT, part INT)
    RETURNS BYTEA
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out BYTEA;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT p.data INTO out
            FROM web_backup_parts p JOIN web_backups b
                ON b.guild_id = p.guild_id AND b.id = p.backup_id
            WHERE p.guild_id = g AND p.backup_id = bid AND p.n = part AND p.data IS NOT NULL
              AND b.campaign_id = c AND b.user_id = dmbot_current_user() AND b.state = 'done'
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900
            FOR UPDATE OF p;
        IF out IS NULL THEN
            RETURN NULL;
        END IF;
        UPDATE web_backup_parts p SET data = NULL, size = 0
            WHERE p.guild_id = g AND p.backup_id = bid AND p.n = part;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_backup_take_part(BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
    """
)

TOPIC_CHOICES = """
    -- Who put a line back (or left it out again) after the session, and what the off-topic filter
    -- turned back into game talk (#2567, docs/PLAN.md, "Off-topic talk"). Three columns on a
    -- line, all defaulted, so no row changes and no backfill policy is needed:
    --   topic_by / topic_set_at: the DM's choice, final: the filter never flips such a line again;
    --   leaned: the filter's own "when unsure, keep it" pass turned this line back to game;
    --   reread: a put-back line the names and story-memory readers have not read yet.
    -- A line's words are not touched. The raw transcript is unchanged.
    ALTER TABLE transcript_lines
        ADD COLUMN topic_by     BIGINT,
        ADD COLUMN topic_set_at BIGINT,
        ADD COLUMN leaned       BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN reread       BOOLEAN NOT NULL DEFAULT FALSE;

    -- dmbot_web_transcript_stretch: the as-heard words of one person's stretch that was left out,
    -- put back or turned back by the filter, at most 200 lines, for whoever may read this session
    -- (anyone in the server, as for the lines). Only that person's spoken lines between the two
    -- start times, and only ones the filter or a DM touched. NULL when the caller is not in the
    -- server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_stretch(
        g BIGINT, c TEXT, sid TEXT, who BIGINT, from_ms BIGINT, to_ms BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('lines', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('startedMs', x.started_ms, 'heard', x.heard)
                             ORDER BY x.started_ms, x.id)
            FROM (SELECT l.id, l.started_ms, l.heard FROM transcript_lines l
                  WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                    AND l.sidebar IS NULL AND NOT l.typed
                    AND l.started_ms BETWEEN from_ms AND to_ms
                    AND (l.topic = 'off_topic' OR l.leaned OR l.topic_by IS NOT NULL)
                  ORDER BY l.started_ms, l.id LIMIT 200) x),
            '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_choices: how the filter treated a session's spoken lines, and the
    -- stretches a DM decided, for whoever may read the session. Counts only, and each decided
    -- stretch as who, from, to and how many lines (a batch is one person's lines set at one moment
    -- in one direction). NULL when the caller is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_choices(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'kept', (SELECT count(*) FROM transcript_lines l
                     WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                       AND NOT l.typed AND l.topic <> 'off_topic' AND NOT l.leaned
                       AND l.topic_by IS NULL),
            'leanedBack', (SELECT count(*) FROM transcript_lines l
                           WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                             AND NOT l.typed AND l.leaned AND l.topic_by IS NULL),
            'leftOut', (SELECT count(*) FROM transcript_lines l
                        WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                          AND NOT l.typed AND l.topic = 'off_topic'),
            'putBack', (SELECT count(*) FROM transcript_lines l
                        WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                          AND NOT l.typed AND l.topic <> 'off_topic' AND l.topic_by IS NOT NULL),
            'choices', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'speaker', x.user_id::TEXT, 'fromMs', x.first_ms, 'toMs', x.last_ms,
                           'lines', x.n, 'putBack', x.put_back) ORDER BY x.first_ms)
                FROM (SELECT l.user_id, l.topic_set_at, (l.topic <> 'off_topic') AS put_back,
                             min(l.started_ms) AS first_ms, max(l.started_ms) AS last_ms,
                             count(*) AS n
                      FROM transcript_lines l
                      WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                        AND NOT l.typed AND l.topic_by IS NOT NULL
                      GROUP BY l.user_id, l.topic_set_at, (l.topic <> 'off_topic')
                      ORDER BY min(l.started_ms) LIMIT 200) x),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_choose: a DM of the campaign puts one person's stretch back (the lines
    -- count as game talk, so the cleaned file and its downloads include them and the readers take
    -- them up) or leaves it out again. Only that person's spoken lines between the two start
    -- times; "put back" changes lines that are left out, "leave out" only lines a DM put back,
    -- never a plain game line or one the filter itself kept. Who and when are kept. Answers the
    -- number of lines changed, 'not_allowed' for anyone who is not a DM of the campaign, NULL
    -- when the caller is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_choose(
        g BIGINT, c TEXT, sid TEXT, who BIGINT, from_ms BIGINT, to_ms BIGINT, back BOOLEAN)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        n INT;
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        IF back THEN
            UPDATE transcript_lines l SET topic = 'game', topic_by = me, topic_set_at = now_s,
                   leaned = FALSE, reread = TRUE
                WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                  AND l.sidebar IS NULL AND NOT l.typed
                  AND l.started_ms BETWEEN from_ms AND to_ms AND l.topic = 'off_topic';
        ELSE
            UPDATE transcript_lines l SET topic = 'off_topic', topic_by = me, topic_set_at = now_s,
                   reread = FALSE
                WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                  AND l.sidebar IS NULL AND NOT l.typed
                  AND l.started_ms BETWEEN from_ms AND to_ms AND l.topic = 'game'
                  AND l.topic_by IS NOT NULL;
        END IF;
        GET DIAGNOSTICS n = ROW_COUNT;
        RETURN jsonb_build_object('status', 'ok', 'changed', n);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_stretch'
            '(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_choices(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_choose'
            '(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_stretch(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_choices(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_choose
        (BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN) FROM PUBLIC;
"""


# The Story graph's overview (#2632): every name the caller may see, and which pairs of names are
# connected, so the page can say "51 names", list the names with no connection yet beside the
# graph and offer every group of linked names. The graph door (above) starts at the most-connected
# name and walks only its confirmed connections, so a young campaign drew 2 names of 51. This door
# reads the same names and the same connections by the same rules: its first half is cut out of
# that door's own text (the checks, the view, `vis` and `rel_ids`), so the two can never disagree
# about who sees what. A test compares them. Read-only: no row changes, no table, nothing in a
# backup.
# (Frozen at the graph door's text as of RELATION_DIRECTIONAL: a later migration that redefines
# `dmbot_web_relationships` must change what this slices from, or the two doors would disagree.)
_overview_door = RELATION_DIRECTIONAL[
    RELATION_DIRECTIONAL.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships(") :
]
_overview_checks = _overview_door[
    _overview_door.index("IF dmbot_current_guild() IS DISTINCT FROM g") : _overview_door.index(
        "IF start_id IS NULL THEN"
    )
]
assert "vis := ARRAY(" in _overview_checks and "rel_ids := ARRAY(" in _overview_checks
# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
GRAPH_OVERVIEW = (
    """
    CREATE OR REPLACE FUNCTION dmbot_web_graph_overview(
        g BIGINT, c TEXT, cap_names INT, cap_pairs INT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        sees_all BOOLEAN;
        wanted TEXT;
        screen TEXT;
        vis TEXT[];
        rel_ids TEXT[];
    BEGIN
        """
    + _overview_checks
    + """
        RETURN jsonb_build_object(
            'status', 'ok', 'canEdit', is_dm AND sees_all, 'seesAll', sees_all,
            'view', CASE WHEN sees_all THEN 'full' ELSE 'players' END,
            'visibility', screen, 'canPeek', is_dm OR screen IN ('peek', 'open'),
            'total', COALESCE(array_length(vis, 1), 0),
            'connections', COALESCE(array_length(rel_ids, 1), 0),
            -- the names, as the graph's nodes tell them; at most `cap_names` (`total` says if more)
            'names', COALESCE((
                SELECT jsonb_agg(x.j ORDER BY x.n, x.id) FROM (
                    SELECT e.id, lower(e.name) AS n, jsonb_build_object(
                        'id', e.id, 'name', e.name,
                        'revealed', NOT (e.hidden_from_players OR e.audience_limited),
                        'kind', CASE WHEN e.type = 'character' THEN
                                    CASE e.role WHEN 'player_character' THEN 'player_character'
                                                WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                                ELSE 'character' END
                                ELSE e.type END,
                        'secret', CASE WHEN sees_all THEN EXISTS (
                            SELECT 1 FROM memory_aliases a
                            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                              AND a.status <> 'rejected' AND a.secret
                              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')) END)
                        AS j
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (vis)
                    ORDER BY lower(e.name), e.id LIMIT GREATEST(cap_names, 1)) x), '[]'::jsonb),
            -- which two names each visible connection joins, once for each pair, in a fixed order
            -- so a cut list is the same on every load; `pairsMore` says if pairs were cut
            'pairs', COALESCE((
                SELECT jsonb_agg(q.p ORDER BY q.a, q.b) FROM (
                    SELECT DISTINCT LEAST(r.subject_id, r.object_id) AS a,
                                    GREATEST(r.subject_id, r.object_id) AS b,
                                    jsonb_build_array(LEAST(r.subject_id, r.object_id),
                                                      GREATEST(r.subject_id, r.object_id)) AS p
                    FROM memory_relations r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                    ORDER BY 1, 2 LIMIT GREATEST(cap_pairs, 1)) q), '[]'::jsonb),
            'pairsMore', (
                SELECT count(*) > GREATEST(cap_pairs, 1) FROM (
                    SELECT DISTINCT LEAST(r.subject_id, r.object_id),
                                    GREATEST(r.subject_id, r.object_id)
                    FROM memory_relations r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)) d));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_overview(BIGINT, TEXT, INT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_graph_overview(BIGINT, TEXT, INT, INT) FROM PUBLIC;
    """
)

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

BACKSTORY_DECIDED = """
    -- How a pronoun in a lore read was settled (#2790): fixed words and a rounded score only
    -- ("who-is-it: accepted 95%", "who-is-it: not checked"), never a name or any text. Kept with a
    -- waiting connection or question so the DM sees DMbot's check beside the quote. No existing row
    -- changes (the default is empty).
    ALTER TABLE backstory_links
        ADD COLUMN decided TEXT NOT NULL DEFAULT '' CHECK (char_length(decided) <= 60);
    ALTER TABLE backstory_which
        ADD COLUMN decided TEXT NOT NULL DEFAULT '' CHECK (char_length(decided) <= 60);

    -- The same note on the kept relationship, for the DM's card only (never in a door, a search or
    -- a player's view): NULL unless the relationship came from a pronoun DMbot settled. It is
    -- carried in the change log, merges (the row is kept) and backups.
    ALTER TABLE memory_relations
        ADD COLUMN decided TEXT CHECK (decided IS NULL OR char_length(decided) <= 60);
"""

BACKSTORY_WHICH = """
    -- A backstory connection whose one end fits two names equally ("Mara" when the campaign has
    -- Mara Ashgrove and Mara Voss) waits here for the DM to pick (#2650), instead of being left
    -- out. It holds the end that is a name (`other_id`, and which side of the connection it is),
    -- the end as written (`word`: the DM's own text, a name), the candidate entry ids and the
    -- same short quote and flags a waiting connection holds. Picking one moves it to
    -- `backstory_links` (ready as soon as both names are answered); "Neither" deletes it. Never
    -- the text itself. Same lifetime as `backstory_links`: deleted with the campaign or its name,
    -- left out of backups (a wait, not content). Its foreign keys follow a campaign that moves
    -- to another server (ON UPDATE CASCADE).
    CREATE TABLE backstory_which (
        guild_id         BIGINT NOT NULL,
        campaign_id      TEXT NOT NULL,
        id               TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$'),
        other_id         TEXT NOT NULL,
        other_is_subject BOOLEAN NOT NULL,
        predicate        TEXT NOT NULL CHECK (char_length(predicate) BETWEEN 1 AND 80),
        word             TEXT NOT NULL CHECK (char_length(word) BETWEEN 1 AND 80),
        candidates       TEXT[] NOT NULL CHECK (cardinality(candidates) BETWEEN 2 AND 10),
        quote            TEXT NOT NULL CHECK (char_length(quote) <= 400),
        secret           BOOLEAN NOT NULL DEFAULT false,
        detail           TEXT NOT NULL DEFAULT '',
        truth            BOOLEAN NOT NULL DEFAULT false,
        upload           TEXT NOT NULL DEFAULT '' CHECK (char_length(upload) <= 120),
        created_at       BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, other_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX backstory_which_once ON backstory_which
        (guild_id, campaign_id, other_id, other_is_subject, predicate, word);
    """ + _isolate("backstory_which")


def _approve_with_versions() -> str:
    """`dmbot_recap_approve` (RECAP_APPROVAL) as it was, plus a call to `dmbot_recap_keep_version`
    (#2122). Taken from the earlier migration's text, so the two cannot drift apart."""
    start = RECAP_APPROVAL.index("    CREATE FUNCTION dmbot_recap_approve(")
    end = RECAP_APPROVAL.index("    $fn$;", start) + len("    $fn$;")
    body = RECAP_APPROVAL[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    declared = "        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;\n"
    keep = "        RETURN 'approved';\n    END"
    if declared not in body or keep not in body:  # (an earlier migration's text moved)
        raise RuntimeError("dmbot_recap_approve changed: the version step needs a new anchor")
    return body.replace(
        keep,
        "        -- The text the DM approved becomes the session's summary in the campaign notes.\n"
        "        PERFORM dmbot_recap_keep_version(g, c, at_, dmbot_current_user(), now_s);\n"
        + keep,
        1,
    )


RECAP_ACCEPTED = (
    """
    -- Each version of a recap text the DM approved (#2122, docs/PLAN.md, "PlotBot checks every
    -- recap"): it becomes that session's summary in the campaign notes, which PlotBot, the
    -- pre-session note, recall and later checks read (the newest version per session). Never
    -- overwritten: an approval of different text adds the next version. A new table, so no rows
    -- change and no backfill policy; rows go with their campaign.
    CREATE TABLE recap_accepted (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        version     INTEGER NOT NULL CHECK (version >= 1),
        text_hash   TEXT NOT NULL,
        title       TEXT NOT NULL,
        summary     TEXT NOT NULL,
        accepted_by BIGINT NOT NULL,
        accepted_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_at, version),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("recap_accepted")
    + """
    -- Keeps the job's current text as the session's next accepted version, unless it is the newest
    -- one again. One place for both ways of approving (the website's door and the Discord button).
    -- A session keeps version 1 and the 19 newest; at the limit the oldest after version 1 goes
    -- to make room. A stored version is never rewritten, and version numbers only go up.
    CREATE FUNCTION dmbot_recap_keep_version(g BIGINT, c TEXT, at_ BIGINT, by BIGINT, now_s BIGINT)
    RETURNS VOID LANGUAGE plpgsql AS $fn$
    DECLARE
        job RECORD;
        h TEXT;
        last_hash TEXT;
        last_version INT;
        total INT;
    BEGIN
        SELECT r.title, r.summary INTO job FROM recap_jobs r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        SELECT a.text_hash, a.version INTO last_hash, last_version FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
            ORDER BY a.version DESC LIMIT 1;
        IF FOUND AND last_hash = h THEN
            RETURN;
        END IF;
        SELECT count(*) INTO total FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_;
        IF total >= 20 THEN
            DELETE FROM recap_accepted WHERE guild_id = g AND campaign_id = c
                AND session_at = at_ AND version = (SELECT min(a.version) FROM recap_accepted a
                    WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
                    AND a.version > 1);
        END IF;
        INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version, text_hash, title,
            summary, accepted_by, accepted_at)
        VALUES (g, c, at_, COALESCE(last_version, 0) + 1, h, job.title, job.summary, by, now_s);
    END
    $fn$;
    REVOKE ALL ON FUNCTION dmbot_recap_keep_version(BIGINT, TEXT, BIGINT, BIGINT, BIGINT)
        FROM PUBLIC;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_keep_version(BIGINT, TEXT, BIGINT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
    + _approve_with_versions()
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)


def _approve_with_plot() -> str:
    """`dmbot_recap_approve` as 0209 left it, plus the plot gate (#2122): when the website sets
    `dmbot.plot_gate` for this transaction, a text whose finished check has an issue with no
    answer is not approved ('plot_open'). It is read under the same lock as the approval, so an
    issue found between the page's read and the press cannot slip through. A check that is
    running, failed or missing never holds it (PlotBot advises; the DM decides)."""
    body = _approve_with_versions()
    anchor = "        -- While the audio is queued or being made nothing is approved over it"
    if body.count(anchor) != 1:  # (an earlier migration's text moved)
        raise RuntimeError("dmbot_recap_approve changed: the plot gate needs a new anchor")
    gate = (
        "        IF current_setting('dmbot.plot_gate', true) = 'on' AND EXISTS (\n"
        "            SELECT 1 FROM plot_checks k JOIN plot_issues i\n"
        "                ON i.guild_id = k.guild_id AND i.campaign_id = k.campaign_id\n"
        "                AND i.session_at = k.session_at AND i.text_hash = k.text_hash\n"
        "            WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_\n"
        "                AND k.text_hash = expected AND k.state = 'done'\n"
        "                AND i.answer_kind IS NULL) THEN\n"
        "            RETURN 'plot_open';\n"
        "        END IF;\n"
    )
    return body.replace(anchor, gate + anchor, 1)


PLOT_ISSUES = (
    """
    -- PlotBot's check of a recap against the campaign (#2122, docs/PLAN.md, "PlotBot checks every
    -- recap before it is accepted"). A check is for one text of a recap (its hash); the issues it
    -- found belong to that text and to the DM alone. New tables, so no rows change and no backfill
    -- policy; rows go with their campaign.
    -- (`plot_checks` is left out of campaign backups on purpose: it only says that a text was
    -- checked and when, and a restored campaign is checked afresh. The issues and the "not a
    -- problem" notes are backed up: they hold the DM's answers.)
    CREATE TABLE plot_checks (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        text_hash   TEXT NOT NULL,
        state       TEXT NOT NULL CHECK (state IN ('running', 'done', 'failed')),
        tries       INTEGER NOT NULL DEFAULT 1,
        calls       INTEGER NOT NULL DEFAULT 0,
        created_at  BIGINT NOT NULL,
        finished_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_at, text_hash),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE TABLE plot_issues (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        id             TEXT NOT NULL,
        session_at     BIGINT NOT NULL,
        text_hash      TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK (kind IN ('contradiction', 'dead_acts', 'item_twice',
                           'place', 'timeline', 'dropped_thread', 'promise', 'secret')),
        level          TEXT NOT NULL CHECK (level IN ('possible', 'probable', 'likely')),
        sentence       INTEGER NOT NULL CHECK (sentence >= 1),
        sentence_text  TEXT NOT NULL,
        reason         TEXT NOT NULL,
        source_text    TEXT NOT NULL,
        source_session TEXT,
        fine_key       TEXT NOT NULL,
        created_at     BIGINT NOT NULL,
        answer_kind    TEXT CHECK (answer_kind IN ('fix', 'changed', 'fine', 'leave')),
        answer_note    TEXT NOT NULL DEFAULT '',
        answered_by    BIGINT,
        answered_at    BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX plot_issues_by_text ON plot_issues (guild_id, campaign_id, session_at, text_hash);
    -- "Not a problem": the DM's note, kept so the same issue is not raised again.
    CREATE TABLE plot_fine (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        fine_key    TEXT NOT NULL,
        note        TEXT NOT NULL,
        by_user     BIGINT NOT NULL,
        at          BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, fine_key),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("plot_checks")
    + _isolate("plot_issues")
    + _isolate("plot_fine")
    + """
    -- The website reads a recap's plot check, and answers its issues, through these two doors:
    -- only a DM of the campaign, in a server on the session's list. The website's role has no
    -- right to the tables themselves. `wait_s`: how long a text may wait for its first check
    -- before the page is told the check is unavailable (the bot may be off): PlotBot advises and
    -- never holds a DM up for long.
    CREATE FUNCTION dmbot_plot_read(g BIGINT, c TEXT, at_ BIGINT, wait_s BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        chk plot_checks%ROWTYPE;
        h TEXT;
        st TEXT;
        issues JSONB;
        vers JSONB;
        open_n INT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        SELECT * INTO chk FROM plot_checks
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ AND text_hash = h;
        IF NOT FOUND THEN
            st := CASE WHEN now_s - job.updated_at > wait_s THEN 'unavailable' ELSE 'checking' END;
        ELSE
            st := CASE chk.state WHEN 'done' THEN 'ready' WHEN 'running' THEN 'checking'
                  ELSE 'unavailable' END;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'id', i.id, 'kind', i.kind, 'level', i.level, 'sentence', i.sentence,
                   'sentenceText', i.sentence_text, 'reason', i.reason,
                   'source', jsonb_build_object('text', i.source_text,
                                                'session', i.source_session),
                   'answer', CASE WHEN i.answer_kind IS NULL THEN NULL
                                  ELSE jsonb_build_object('kind', i.answer_kind,
                                                          'note', i.answer_note) END)
                   ORDER BY i.sentence, i.id), '[]'::jsonb),
               COUNT(*) FILTER (WHERE i.answer_kind IS NULL)
            INTO issues, open_n
            FROM plot_issues i
            WHERE i.guild_id = g AND i.campaign_id = c AND i.session_at = at_
              AND i.text_hash = h AND st = 'ready';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'version', a.version, 'acceptedAt', a.accepted_at)
                   ORDER BY a.version), '[]'::jsonb)
            INTO vers FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_;
        RETURN jsonb_build_object('state', st, 'hash', h, 'issues', issues,
            'versions', vers, 'open', COALESCE(open_n, 0),
            'approvedCurrent', COALESCE((SELECT a.text_hash = h FROM recap_accepted a
                WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
                ORDER BY a.version DESC LIMIT 1), false));
    END
    $fn$;
    CREATE FUNCTION dmbot_plot_answer(
        g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, p_kind TEXT, p_note TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        iss plot_issues%ROWTYPE;
        h TEXT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF p_kind IS NULL OR p_kind NOT IN ('fix', 'changed', 'fine', 'leave')
           OR p_note IS NULL OR length(p_note) > 500 THEN
            RETURN 'bad';
        END IF;
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_found';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_found';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        SELECT * INTO iss FROM plot_issues
            WHERE guild_id = g AND campaign_id = c AND id = p_issue AND session_at = at_
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        IF iss.text_hash <> h THEN
            RETURN 'stale';  -- the text changed since: a new check has its own issues
        END IF;
        UPDATE plot_issues SET answer_kind = p_kind, answer_note = p_note,
            answered_by = dmbot_current_user(), answered_at = now_s
            WHERE guild_id = g AND campaign_id = c AND id = p_issue;
        IF p_kind = 'fine' THEN
            INSERT INTO plot_fine (guild_id, campaign_id, fine_key, note, by_user, at)
            VALUES (g, c, iss.fine_key, p_note, dmbot_current_user(), now_s)
            ON CONFLICT (guild_id, campaign_id, fine_key)
            DO UPDATE SET note = EXCLUDED.note, by_user = EXCLUDED.by_user, at = EXCLUDED.at;
        ELSE
            DELETE FROM plot_fine
                WHERE guild_id = g AND campaign_id = c AND fine_key = iss.fine_key;
        END IF;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    """
    + _approve_with_plot()
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)

PLOT_CHAT = (
    f"""
    -- The DM's talk with PlotBot about one plot issue (#2123, docs/PLAN.md, "PlotBot checks every
    -- recap"). The website has no AI key, so the DM's message is queued here as a row, the bot
    -- answers on a second row (with the changes it proposes, if any), and the page polls. The talk
    -- is the DM's own: a row is read by the DM who wrote it and no one else, is never in a
    -- transcript or on a public link, and is never logged. It stays with its issue and goes with
    -- the campaign; it is left out of backups. `proposals`: a list of {{id, kind, label, data,
    -- state}} (state open, saving, saved, declined or failed): nothing is saved until the DM
    -- presses Save. New table: no rows change, so no backfill policy.
    CREATE TABLE plot_chat (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        issue_id    TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        role        TEXT NOT NULL CHECK (role IN ('dm', 'bot')),
        body        TEXT NOT NULL DEFAULT '' CHECK (char_length(body) <= 2000),
        state       TEXT NOT NULL DEFAULT 'done'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        proposals   JSONB NOT NULL DEFAULT '[]'
            CHECK (jsonb_typeof(proposals) = 'array' AND jsonb_array_length(proposals) <= 6),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX plot_chat_by_issue ON plot_chat (guild_id, campaign_id, issue_id, created_at);
    CREATE INDEX plot_chat_open ON plot_chat (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("plot_chat")
    + r"""
    -- The website's role has no grant on the table; these doors each check the caller is a DM of
    -- the campaign in a server on the session's list, and a DM sees only their own messages.
    CREATE FUNCTION dmbot_plot_chat_send(g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        iss plot_issues%ROWTYPE;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT * INTO iss FROM plot_issues
            WHERE guild_id = g AND campaign_id = c AND id = p_issue AND session_at = at_;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF iss.text_hash <> encode(sha256(convert_to(job.title || E'\n' || job.summary, 'UTF8')),
                                   'hex') THEN
            RETURN jsonb_build_object('status', 'stale');
        END IF;
        -- A talk is kept for thirty days; older words of this campaign go here.
        DELETE FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.created_at < now_s - 2592000;
        -- A message the bot never answered (it was off, or died) is given up after ten minutes,
        -- so the DM is never stuck behind it.
        UPDATE plot_chat SET state = 'failed', error = 'timed_out'
            WHERE guild_id = g AND campaign_id = c AND user_id = dmbot_current_user()
              AND role = 'dm' AND state IN ('waiting', 'working') AND created_at < now_s - 600;
        -- One at a time, and a bounded talk: PlotBot is quick, not a chat room.
        IF EXISTS (SELECT 1 FROM plot_chat m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
                     AND m.user_id = dmbot_current_user()
                     AND m.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
              AND m.user_id = dmbot_current_user() AND m.role = 'dm') >= 15 THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        -- The campaign's whole hour: at most thirty messages from its DMs (each is a careful-tier
        -- call; docs/costs.md), however many issues they are on.
        IF (SELECT count(*) FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.role = 'dm'
              AND m.created_at > now_s - 3600) >= 30 THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        INSERT INTO plot_chat (guild_id, campaign_id, id, issue_id, session_at, user_id, role,
                               body, state, created_at)
            VALUES (g, c, rid, p_issue, at_, dmbot_current_user(), 'dm', btrim(txt), 'waiting',
                    now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    CREATE FUNCTION dmbot_plot_chat_read(g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user())
           OR NOT EXISTS (SELECT 1 FROM plot_issues i
                          WHERE i.guild_id = g AND i.campaign_id = c AND i.id = p_issue
                            AND i.session_at = at_) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('messages', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', m.id, 'role', m.role, 'text', m.body, 'state', m.state,
                       'error', m.error, 'proposals', m.proposals, 'at', m.created_at)
                   ORDER BY m.created_at, m.role DESC, m.id)
            FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
              AND m.session_at = at_ AND m.user_id = dmbot_current_user()), '[]'::jsonb));
    END
    $fn$;

    -- Save or Not that, on one proposal of PlotBot's. Not that closes it. Save only marks it
    -- 'saving': the bot makes the change, as the DM's own fact, and then marks it saved. A
    -- proposal that is no longer open is 'done_already'.
    CREATE FUNCTION dmbot_plot_proposal(
        g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, p_prop TEXT, p_action TEXT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        m plot_chat%ROWTYPE;
        want TEXT;
    BEGIN
        IF p_action IS NULL OR p_action NOT IN ('save', 'decline') THEN
            RETURN 'bad';
        END IF;
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_found';
        END IF;
        -- (a proposal's id is a fresh random id, so it names one proposal of one message)
        SELECT * INTO m FROM plot_chat x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.role = 'bot'
              AND x.issue_id = p_issue AND x.session_at = at_
              AND x.user_id = dmbot_current_user()
              AND x.proposals @> jsonb_build_array(jsonb_build_object('id', p_prop))
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        IF NOT m.proposals @> jsonb_build_array(
                jsonb_build_object('id', p_prop, 'state', 'open')) THEN
            RETURN 'done_already';
        END IF;
        -- A proposal is about the words PlotBot read: if the recap changed since, the page
        -- reloads and a new check has its own issues.
        IF p_action = 'save' AND NOT EXISTS (
                SELECT 1 FROM recap_jobs j JOIN plot_issues i
                    ON i.guild_id = j.guild_id AND i.campaign_id = j.campaign_id
                    AND i.session_at = j.session_at
                WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                    AND i.id = p_issue
                    AND i.text_hash = encode(sha256(convert_to(j.title || E'\n' || j.summary,
                                                               'UTF8')), 'hex')) THEN
            RETURN 'stale';
        END IF;
        want := CASE p_action WHEN 'save' THEN 'saving' ELSE 'declined' END;
        UPDATE plot_chat SET proposals = (
            SELECT jsonb_agg(CASE WHEN e->>'id' = p_prop
                                  THEN jsonb_set(e, '{state}', to_jsonb(want)) ELSE e END
                             ORDER BY ord)
            FROM jsonb_array_elements(m.proposals) WITH ORDINALITY AS t(e, ord))
            WHERE guild_id = g AND campaign_id = c AND id = m.id;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)
        FROM PUBLIC;
    """
)


def _plot_chat_followups() -> str:
    """The three talk doors of 0211, re-made with small changes (#2545), cut out of the text that
    made them so nothing else about them can drift: the campaign's hourly cap answers its own
    code (`hour_full`, so the page can say it is the hour and not the issue that is full); a Save
    is stamped with its time (`saving_at`); and a proposal still `saving` thirty minutes after
    that (or after its message, for one saved before this) is shown as failed when the DM next
    looks, so a Save made while PlotBot is off does not wait forever. The two give-ups (a message
    never answered, a Save never made) write a plain sentence as their error, not a code: the
    page prints errors word for word."""
    start = PLOT_CHAT.index("    CREATE FUNCTION dmbot_plot_chat_send")
    end = PLOT_CHAT.index("    DO $do$")
    doors = PLOT_CHAT[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    now = "floor(extract(epoch FROM clock_timestamp()))::BIGINT"
    edits = (
        (
            "        UPDATE plot_chat SET state = 'failed', error = 'timed_out'\n",
            "        UPDATE plot_chat SET state = 'failed',\n"
            "                             error = 'PlotBot didn''t answer. Ask it again.'\n",
        ),
        (
            "              AND m.created_at > now_s - 3600) >= 30 THEN\n"
            "            RETURN jsonb_build_object('status', 'too_many');",
            "              AND m.created_at > now_s - 3600) >= 30 THEN\n"
            "            RETURN jsonb_build_object('status', 'hour_full');",
        ),
        (
            "THEN jsonb_set(e, '{state}', to_jsonb(want)) ELSE e END",
            "THEN jsonb_set(e, '{state}', to_jsonb(want)) || CASE WHEN want = 'saving'\n"
            f"                THEN jsonb_build_object('saving_at', {now})\n"
            "                ELSE '{}'::jsonb END ELSE e END",
        ),
        (
            "        RETURN jsonb_build_object('messages', COALESCE((",
            "        -- A Save the bot has not made in thirty minutes (it was off) is given up.\n"
            "        UPDATE plot_chat SET proposals = (\n"
            "            SELECT jsonb_agg(CASE WHEN e->>'state' = 'saving'\n"
            "                    AND COALESCE((e->>'saving_at')::BIGINT, created_at)\n"
            f"                        < {now} - 1800\n"
            "                THEN e || jsonb_build_object('state', 'failed', 'error',\n"
            "                    'PlotBot didn''t save this in time. Ask it again.')\n"
            "                ELSE e END ORDER BY ord)\n"
            "            FROM jsonb_array_elements(proposals) WITH ORDINALITY AS t(e, ord))\n"
            "            WHERE guild_id = g AND campaign_id = c AND issue_id = p_issue\n"
            "              AND session_at = at_ AND user_id = dmbot_current_user()\n"
            "              AND role = 'bot'\n"
            "              AND EXISTS (SELECT 1 FROM jsonb_array_elements(proposals) x\n"
            "                  WHERE x->>'state' = 'saving'\n"
            "                    AND COALESCE((x->>'saving_at')::BIGINT, created_at)\n"
            f"                        < {now} - 1800);\n"
            "        RETURN jsonb_build_object('messages', COALESCE((",
        ),
    )
    for old, new in edits:
        if doors.count(old) != 1:  # (0211's text moved)
            raise RuntimeError("the plot chat doors changed: the follow-ups need a new anchor")
        doors = doors.replace(old, new, 1)
    # Replacing a function drops its pinned search path, so the pin is made again (0211's block).
    return (
        doors + PLOT_CHAT[end : PLOT_CHAT.index("    REVOKE ALL ON FUNCTION dmbot_plot_chat_send")]
    )


PLOT_CHAT_FOLLOWUPS = _plot_chat_followups()

# A person the story describes but never names (#2790): "Ken's sister", "a hooded figure". The row
# is a proposed character with no alias, so no name match can find it; `unnamed_what` is what the
# story calls them ("sister"), `unnamed_anchor` the entry they hang off (Ken; none for a hooded
# figure) and `unnamed_role` the relationship (sibling). The label is built from these when it is
# shown, so a rename or a merge of the anchor never leaves it stale. The key declares ON UPDATE
# CASCADE itself, because it carries guild_id (CLAUDE.md, #2550).
UNNAMED_PEOPLE = """
    ALTER TABLE memory_entities
        ADD COLUMN unnamed BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN unnamed_anchor TEXT,
        ADD COLUMN unnamed_role TEXT,
        ADD COLUMN unnamed_what TEXT;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_unnamed_shape CHECK (
        unnamed = (unnamed_what IS NOT NULL)
        AND (unnamed OR (unnamed_anchor IS NULL AND unnamed_role IS NULL))
        AND (unnamed_what IS NULL OR length(unnamed_what) BETWEEN 1 AND 100)
        AND (unnamed_role IS NULL OR length(unnamed_role) BETWEEN 1 AND 40)
        AND (unnamed_anchor IS NULL OR unnamed_anchor <> id));
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_unnamed_anchor_link
        FOREIGN KEY (guild_id, campaign_id, unnamed_anchor)
        REFERENCES memory_entities (guild_id, campaign_id, id)
        ON UPDATE CASCADE DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX memory_entities_by_unnamed_anchor
        ON memory_entities (guild_id, campaign_id, unnamed_anchor)
        WHERE unnamed_anchor IS NOT NULL;
    CREATE INDEX memory_entities_unnamed
        ON memory_entities (guild_id, campaign_id) WHERE unnamed;
"""

# Every function that hides the DM's entries from players by `hidden_from_players` also hides an
# unnamed person (#2790): a confirmed one must never show on the website, in the search text or in
# a recap. Each is read back from the database as it is now, three spellings of the same guard are
# widened, and the function is made again with every setting it had (its pinned search path, its
# rights). The spellings, x being the table's alias: `NOT x.hidden_from_players`,
# `OR x.hidden_from_players)` and `(x.hidden_from_players OR`. A function where none changes has a
# guard this migration missed, and stops the migration instead of passing silently.
UNNAMED_HIDDEN_FROM_PLAYERS = r"""
    DO $$
    DECLARE
        r record;
        def text;
        fresh text;
        have int;
        paired int;
    BEGIN
        FOR r IN
            SELECT p.oid, p.proname FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname IN (
                  'dmbot_entity_parts', 'dmbot_recap_check_names', 'dmbot_recap_shared',
                  'dmbot_web_all_names', 'dmbot_web_campaign_facts', 'dmbot_web_graph_overview',
                  'dmbot_web_relationships', 'dmbot_web_story_timeline')
            ORDER BY p.proname
        LOOP
            def := pg_get_functiondef(r.oid);
            fresh := regexp_replace(def, 'NOT (\w+)\.hidden_from_players',
                                    'NOT \1.hidden_from_players AND NOT \1.unnamed', 'g');
            fresh := regexp_replace(fresh, 'OR (\w+)\.hidden_from_players\)',
                                    'OR \1.hidden_from_players OR \1.unnamed)', 'g');
            fresh := regexp_replace(fresh, '\((\w+)\.hidden_from_players OR ',
                                    '(\1.hidden_from_players OR \1.unnamed OR ', 'g');
            IF fresh = def THEN
                RAISE EXCEPTION 'no hidden_from_players guard changed in %', r.proname;
            END IF;
            -- Guard by guard, not function by function: every way of saying "not hidden from
            -- players" left in the new text must have its unnamed partner next to it (a positive
            -- use, such as the one that lists the held names, is no guard and is left alone).
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'NOT \w+\.hidden_from_players', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, 'NOT (\w+)\.hidden_from_players AND NOT \1\.unnamed', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION 'NOT x.hidden_from_players has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'OR \w+\.hidden_from_players\)', 'g');
            IF have <> 0 THEN
                RAISE EXCEPTION 'OR x.hidden_from_players) has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, '\(\w+\.hidden_from_players OR ', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, '\((\w+)\.hidden_from_players OR \1\.unnamed OR ', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION '(x.hidden_from_players OR has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'OR \w+\.hidden_from_players OR ', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, 'OR (\w+)\.hidden_from_players OR \1\.unnamed', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION 'OR x.hidden_from_players OR has no unnamed guard: %', r.proname;
            END IF;
            EXECUTE fresh;
        END LOOP;
    END $$;
"""

WEB_WAITING_ACTIONS_LIST = ", ".join(
    f"'{a}'" for a in (*WAITING_ACTIONS, "backstory-which-pick", "backstory-which-neither")
)
_which_door = LINK_DECIDE_ACTIONS[
    LINK_DECIDE_ACTIONS.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(") :
]
_which_door = _which_door[: _which_door.index("    -- (a redefined function loses")]
for _old, _new in (
    (
        "'backstory-skip', 'backstory-keep-all')\n        THEN",
        "'backstory-skip', 'backstory-keep-all',\n"
        "                'backstory-which-pick', 'backstory-which-neither')\n        THEN",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND clean <> '')",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-neither')\n               AND clean <> '')",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND (tgt IS NULL",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND (tgt IS NULL",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "           OR (act = 'link-words' AND COALESCE(fl, false)) THEN",
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(fl, false))\n"
        "           OR (act = 'link-words' AND COALESCE(fl, false)) THEN",
    ),
    (
        "           OR (act = 'backstory-keep-all' AND (clean = ''",
        "           OR (act = 'backstory-which-pick' AND clean !~ '^[0-9a-f]{32}$')\n"
        "           OR (act = 'backstory-keep-all' AND (clean = ''",
    ),
    (
        "            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (",
        "            ELSIF act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "                   AND NOT EXISTS (\n"
        "                   SELECT 1 FROM backstory_which w WHERE w.guild_id = g\n"
        "                   AND w.campaign_id = c AND w.id = tgt\n"
        "                   AND (act = 'backstory-which-neither'\n"
        "                        OR clean = ANY (w.candidates))) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (",
    ),
):
    assert _which_door.count(_old) == 1, (_old, _which_door.count(_old))
    _which_door = _which_door.replace(_old, _new)

WEB_WAITING = (
    f"""
    -- The website's answer to a "Which Mara?" question (#2652): two more actions in the queue of
    -- the other name actions. 'backstory-which-pick': `target_id` is the waiting question
    -- (`backstory_which`), `text` the entry the DM picked (one of its candidates); and
    -- 'backstory-which-neither': the question is dropped. The bot applies them with
    -- `BackstoryLinks.pick_which`, the call the Discord card makes. No row changes.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}));
    """
    + _which_door
    + r"""
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;

    -- What waits for the DM, for the website (#2652, Supervisor's answer on #2642): the claims
    -- PlotBot found at the table, the connections a backstory found, and the "Which one?"
    -- questions, with the counts. DM only (anyone else gets NULL, as from the other doors). Counts
    -- and the DM's own words (a name, a short quote), never a transcript. A connection to a name
    -- that was refused or merged away is left out: the bot cleans the queue when it next reads it.
    CREATE OR REPLACE FUNCTION dmbot_web_waiting(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        out := jsonb_build_object(
            'status', 'ok',
            'claims', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', m.id, 'subject', m.subject, 'relationship', m.relationship,
                    'object', m.object, 'details', m.details, 'how', m.how,
                    'at', m.session_started_at)
                    ORDER BY m.created_at, m.id)
                FROM (SELECT * FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'
                      ORDER BY k.created_at, k.id LIMIT 200) m), '[]'::jsonb),
            'backstory', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', l.id, 'subjectId', l.subject_id, 'subject', s.name,
                    'objectId', l.object_id, 'object', o.name, 'predicate', l.predicate,
                    'detail', l.detail, 'quote', l.quote,
                    'secret', l.secret OR s.audience_limited OR o.audience_limited,
                    'truth', l.truth, 'upload', l.upload,
                    'ready', s.status = 'confirmed' AND o.status = 'confirmed',
                    'waitingOn', to_jsonb(ARRAY(
                        SELECT x.name
                        FROM (VALUES (s.name, s.status), (o.name, o.status)) x(name, status)
                        WHERE x.status <> 'confirmed')))
                    ORDER BY l.created_at, l.id)
                FROM (SELECT * FROM backstory_links b
                      WHERE b.guild_id = g AND b.campaign_id = c
                      ORDER BY b.created_at, b.id LIMIT 300) l
                JOIN memory_entities s ON s.guild_id = g AND s.campaign_id = c
                                      AND s.id = l.subject_id
                                      AND s.status IN ('confirmed', 'proposed')
                JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c
                                      AND o.id = l.object_id
                                      AND o.status IN ('confirmed', 'proposed')), '[]'::jsonb),
            -- the true sizes, since the lists above are cut at their caps
            'totals', jsonb_build_object(
                'claims', (SELECT count(*) FROM memory_claims k
                           WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                             AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0
                             AND k.reason <> 'waiting for its name to be proposed'),
                'backstory', (SELECT count(*) FROM backstory_links b
                              WHERE b.guild_id = g AND b.campaign_id = c)),
            -- (a question left with fewer than two live candidates is not shown: nothing can be
            -- picked, and the bot deletes it when it next reads the queue)
            'which', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', w.id, 'word', w.word, 'otherId', w.other_id, 'other', o.name,
                    'otherIsSubject', w.other_is_subject, 'predicate', w.predicate,
                    'detail', w.detail, 'quote', w.quote, 'secret', w.secret OR o.audience_limited,
                    'upload', w.upload, 'candidates', w.cands) ORDER BY w.created_at, w.id)
                FROM (
                    SELECT q.*, (
                        SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name,
                                                           'limited', e.audience_limited)
                                         ORDER BY lower(e.name), e.id)
                        FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (q.candidates)
                          AND e.id <> q.other_id AND e.status IN ('confirmed', 'proposed')) AS cands
                    FROM backstory_which q
                    WHERE q.guild_id = g AND q.campaign_id = c) w
                JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c
                                      AND o.id = w.other_id
                                      AND o.status IN ('confirmed', 'proposed')
                WHERE jsonb_array_length(COALESCE(w.cands, '[]'::jsonb)) >= 2), '[]'::jsonb));
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_waiting(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_waiting(BIGINT, TEXT) FROM PUBLIC;
    """
)

MEMORY_EVIDENCE = (
    """
    -- Typed evidence links (#2662, docs/STORY_MEMORY.md 9a, "Evidence links"): what a connection
    -- or a belief stands on, one row for each thing it points at, in place of the untyped lists
    -- `memory_relations.mention_ids` and `memory_beliefs.evidence_ids`. A link is a claim, a
    -- transcript line, a clue or an event (`kind`), the thing's id (`target_id`) and the session
    -- it belongs to. The campaign's composite keys keep a link inside its campaign, to a row of
    -- that campaign: a link to a connection or belief of another campaign cannot be made.
    -- When a person's words are removed (Stop recording me, Delete my past transcripts, the
    -- retention setting, account deletion) the link of a line or claim becomes a PLACEHOLDER:
    -- `target_id` is cleared and the kind and session stay, so "a line from session 12, no longer
    -- available" can still be said, with no words and no speaker. A line's target is its
    -- reference ("session:started_ms:user"), which names the speaker, so clearing it is what
    -- keeps the speaker out. A link never gives access to what it points at: it is read only
    -- through the door that already shows the connection or belief. Unlogged (an Undo does not
    -- bring a link back; a connection that Undo restores has its links in the old list until
    -- that is dropped), and deleted with its connection or belief. Its foreign keys follow a
    -- campaign that moves to another server (ON UPDATE CASCADE).
    CREATE TABLE memory_evidence (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        id                 TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$'),
        relation_id        TEXT,
        belief_id          TEXT,
        kind               TEXT NOT NULL CHECK (kind IN ('claim', 'line', 'clue', 'event')),
        target_id          TEXT CHECK (char_length(target_id) BETWEEN 1 AND 100),
        session_started_at BIGINT,
        added_at           BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((relation_id IS NOT NULL) <> (belief_id IS NOT NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, belief_id)
            REFERENCES memory_beliefs (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_evidence_by_relation
        ON memory_evidence (guild_id, campaign_id, relation_id) WHERE relation_id IS NOT NULL;
    CREATE INDEX memory_evidence_by_belief
        ON memory_evidence (guild_id, campaign_id, belief_id) WHERE belief_id IS NOT NULL;
    CREATE INDEX memory_evidence_by_target
        ON memory_evidence (guild_id, kind, target_id) WHERE target_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_evidence_once_for_relation
        ON memory_evidence (guild_id, campaign_id, relation_id, kind, target_id)
        WHERE relation_id IS NOT NULL AND target_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_evidence_once_for_belief
        ON memory_evidence (guild_id, campaign_id, belief_id, kind, target_id)
        WHERE belief_id IS NOT NULL AND target_id IS NOT NULL;
    """
    + _isolate("memory_evidence")
    + """
    -- The old lists, copied in. Migrations run with no server set, so row-level security hides
    -- every row: open EVERY table the statements read or write, for this migration alone
    -- (CLAUDE.md: a plain INSERT ... SELECT would match nothing).
    CREATE POLICY migrate_backfill ON memory_evidence FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_beliefs FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_mentions FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_claims FOR SELECT USING (true);
    -- A connection's mentions are lines: the link points at the line the mention names (a
    -- placeholder when the mention is already gone, which is what it would have become).
    INSERT INTO memory_evidence
            (guild_id, campaign_id, relation_id, kind, target_id, session_started_at, added_at)
        SELECT DISTINCT ON (r.guild_id, r.campaign_id, r.id, t.line_ref)
               r.guild_id, r.campaign_id, r.id, 'line', t.line_ref, t.session_started_at,
               r.created_at
        FROM memory_relations r
        CROSS JOIN LATERAL unnest(r.mention_ids) AS ids(mention_id)
        LEFT JOIN memory_mentions m ON m.guild_id = r.guild_id AND m.campaign_id = r.campaign_id
                                   AND m.id = ids.mention_id
        CROSS JOIN LATERAL (SELECT CASE WHEN char_length(m.line_ref) <= 100
                                        THEN m.line_ref END AS line_ref,
                                   m.session_started_at AS session_started_at) t
        ORDER BY r.guild_id, r.campaign_id, r.id, t.line_ref, t.session_started_at;
    -- A belief's evidence ids were never typed: one that is a claim of the campaign is a claim,
    -- anything else is taken to be a line.
    INSERT INTO memory_evidence
            (guild_id, campaign_id, belief_id, kind, target_id, session_started_at, added_at)
        SELECT DISTINCT b.guild_id, b.campaign_id, b.id,
               CASE WHEN c.id IS NULL THEN 'line' ELSE 'claim' END, ids.evidence_id,
               b.session_at, b.recorded_at
        FROM memory_beliefs b
        CROSS JOIN LATERAL unnest(b.evidence_ids) AS ids(evidence_id)
        LEFT JOIN memory_claims c ON c.guild_id = b.guild_id AND c.campaign_id = b.campaign_id
                                 AND c.id = ids.evidence_id
        WHERE char_length(ids.evidence_id) BETWEEN 1 AND 100;
    DROP POLICY migrate_backfill ON memory_claims;
    DROP POLICY migrate_backfill ON memory_mentions;
    DROP POLICY migrate_backfill ON memory_beliefs;
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_evidence;
"""
)

_WAITING_DOOR = WEB_WAITING[WEB_WAITING.index("CREATE OR REPLACE FUNCTION dmbot_web_waiting(") :]
CLAIM_READINGS = """
    -- A claim can be a reading (#2412, docs/STORY_MEMORY.md 9 and 9a): not a fact someone said
    -- but a belief for the DM to place, such as a theory, a reveal, a frame-up or what a spell
    -- showed. `reading` holds what the reading adds to the claim's words: the point of view
    -- that holds it, its confidence and status (set by code, never by the model), the clue,
    -- event or spell that justifies it, the act that faked it and whether the DM alone may
    -- write it. Names, numbers and words of the game only, never a Discord id or a quote; a
    -- claim's lines and speakers are the claim's own and go with a person's words as before.
    -- NULL for every claim made before. A new column on a table the website cannot read, so no
    -- row is touched and no policy opened.
    ALTER TABLE memory_claims ADD COLUMN reading JSONB
        CHECK (reading IS NULL OR (jsonb_typeof(reading) = 'object'
                                   AND pg_column_size(reading) <= 2000));
    -- A reading is a belief to place, not a fact to say yes to: the website's list of what waits
    -- for the DM leaves it out (its claims and its count), as the Discord list does. Cut from
    -- the door as 0233 made it, with that one condition added.
""" + _WAITING_DOOR.replace(
    "AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0",
    "AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0 AND k.reading IS NULL",
)
if _WAITING_DOOR.count("AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0") != 2:
    # (an explicit raise: `python -O` drops an assert, and this door must not change silently)
    raise RuntimeError("the waiting door no longer has the two filters 0236 widens")


# What PlotBot made of each contradiction candidate (#2660, docs/STORY_MEMORY.md 9a): its grade,
# major or minor (or "not graded" when the answer was cut or unreadable, never "minor"), and its
# one-line reason. A candidate is two connections ONE point of view holds that may clash; the row
# holds only their ids and the grade, so it has no word of a transcript and no player's words.
# `said_at` is when a major one was told to the DM on the DM screen (so it is told once). Made
# again by the next check, so a backup leaves it out (campaigns/tables.py). Deleted with the
# campaign, the connections or the point of view it names, and its keys follow a campaign that
# moves to another server.
CONTRADICTION_GRADES = """
    CREATE TABLE memory_contradictions (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        perspective_id     TEXT NOT NULL,
        kind               TEXT NOT NULL
            CHECK (kind IN ('contradiction', 'too_many', 'held_twice')),
        first_relation_id  TEXT NOT NULL,
        second_relation_id TEXT NOT NULL,
        grade              TEXT NOT NULL CHECK (grade IN ('major', 'minor', 'not_graded')),
        reason             TEXT NOT NULL DEFAULT '' CHECK (char_length(reason) <= 2000),
        times_unknown      BOOLEAN NOT NULL DEFAULT false,
        involves_guess     BOOLEAN NOT NULL DEFAULT false,
        found_at           BIGINT NOT NULL,
        session_started_at BIGINT,
        said_at            BIGINT,
        PRIMARY KEY (guild_id, campaign_id, perspective_id, kind, first_relation_id,
                     second_relation_id),
        CHECK (first_relation_id < second_relation_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, first_relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, second_relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_contradictions_by_grade
        ON memory_contradictions (guild_id, campaign_id, grade, said_at);
    -- (deleting a connection checks the keys that name it)
    CREATE INDEX memory_contradictions_by_first
        ON memory_contradictions (guild_id, campaign_id, first_relation_id);
    CREATE INDEX memory_contradictions_by_second
        ON memory_contradictions (guild_id, campaign_id, second_relation_id);
    """ + _isolate("memory_contradictions")

# The after-session report's storage (#2661; docs/STORY_MEMORY.md 9a, "The after-session report").
# `memory_differences`: what the party (or a player's character) believes that differs from The
# truth, with PlotBot's sort (disguised, misunderstood, false, or not sorted when the answer was cut
# or unreadable: never "false") and its one-line reason. Ids and DMbot's own words only, like
# `memory_contradictions`: no transcript word, no speaker. The DM's alone. Made again by the next
# check, so a backup leaves it out.
# `report_answers`: the DM's answer to each report item, kept apart from the items because an item
# is made again (and a clash that is no longer found is dropped) while an answer is a decision the
# DM made: "That's intended" must not come back, and a restored campaign keeps them (a backup
# section). Keyed by a hash of the item's own ids, so a re-found item meets its answer again.
# `memory_report_runs`: how far making one session's report has got (stage, counts, times), for
# the website's progress line; runtime state, left out of a backup. Every key that carries the
# server follows a campaign that moves to another one.
REPORT_STORAGE = """
    CREATE TABLE memory_differences (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        perspective_id     TEXT NOT NULL,
        relation_id        TEXT NOT NULL,
        truth_relation_id  TEXT,
        kind               TEXT NOT NULL CHECK (kind IN ('denied', 'clashes')),
        sort               TEXT NOT NULL
            CHECK (sort IN ('disguised', 'misunderstood', 'false', 'not_sorted')),
        reason             TEXT NOT NULL DEFAULT '' CHECK (char_length(reason) <= 2000),
        times_unknown      BOOLEAN NOT NULL DEFAULT false,
        involves_guess     BOOLEAN NOT NULL DEFAULT false,
        found_at           BIGINT NOT NULL,
        session_started_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, perspective_id, relation_id),
        CHECK ((kind = 'denied') = (truth_relation_id IS NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, truth_relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_differences_by_sort
        ON memory_differences (guild_id, campaign_id, sort);
    -- (deleting a connection checks the keys that name it)
    CREATE INDEX memory_differences_by_relation
        ON memory_differences (guild_id, campaign_id, relation_id);
    CREATE INDEX memory_differences_by_truth
        ON memory_differences (guild_id, campaign_id, truth_relation_id);

    CREATE TABLE report_answers (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        item_key     TEXT NOT NULL CHECK (item_key ~ '^[0-9a-f]{32}$'),
        item_kind    TEXT NOT NULL CHECK (item_kind IN ('contradiction', 'difference')),
        answer       TEXT NOT NULL
            CHECK (answer IN ('intended', 'not_a_problem', 'remind', 'fixed')),
        answered_by  BIGINT NOT NULL,
        answered_at  BIGINT NOT NULL,
        remind_from  BIGINT,
        PRIMARY KEY (guild_id, campaign_id, item_key),
        CHECK ((answer = 'remind') = (remind_from IS NOT NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );

    CREATE TABLE memory_report_runs (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        state       TEXT NOT NULL CHECK (state IN ('making', 'ready', 'failed')),
        stage       TEXT NOT NULL CHECK (stage IN ('reading', 'grading', 'sorting')),
        done        INTEGER NOT NULL DEFAULT 0 CHECK (done >= 0),
        total       INTEGER NOT NULL DEFAULT 0 CHECK (total >= 0),
        started_at  BIGINT NOT NULL,
        updated_at  BIGINT NOT NULL,
        reason      TEXT NOT NULL DEFAULT '' CHECK (reason ~ '^[a-z_]{0,40}$'),
        PRIMARY KEY (guild_id, campaign_id, session_at),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + "".join(
    _isolate(t) for t in ("memory_differences", "report_answers", "memory_report_runs")
)

READING_ACTIONS = ("reading-place", "reading-no", "reading-undo")
_READING_LIST = ", ".join(f"'{a}'" for a in READING_ACTIONS)
_reading_door = _which_door
for _old, _new in (
    (
        "'backstory-which-pick', 'backstory-which-neither')\n        THEN",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                {_READING_LIST})\n        THEN",
    ),
    (
        "'backstory-which-neither')\n               AND clean <> '')",
        f"'backstory-which-neither',\n                       {_READING_LIST})\n"
        "               AND clean <> '')",
    ),
    (
        "'backstory-which-pick', 'backstory-which-neither')\n               AND (tgt IS NULL",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND (tgt IS NULL",
    ),
    (
        "'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(fl, false))",
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND COALESCE(fl, false))",
    ),
    (
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' THEN",
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%' THEN",
    ),
    (
        "            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (",
        "            ELSIF act IN ('reading-place', 'reading-no') AND NOT EXISTS (\n"
        "                   SELECT 1 FROM memory_claims m\n"
        "                   WHERE m.guild_id = g AND m.campaign_id = c AND m.id = tgt\n"
        "                   AND m.status = 'proposed' AND m.reading IS NOT NULL) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.id = tgt AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action = 'reading-place' AND p.state = 'done'\n"
        "                   AND p.result_other <> '') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (",
    ),
):
    if _reading_door.count(_old) != 1:  # (an explicit raise: `python -O` drops an assert)
        raise RuntimeError(f"the name-action door changed under the readings migration: {_old!r}")
    _reading_door = _reading_door.replace(_old, _new)

WEB_READINGS = (
    f"""
    -- What people believe, on the website (#2412 slice 3b): the DM keeps a reading as a belief,
    -- says no, or undoes a keep, through the queue of the other name actions. `target_id` is the
    -- waiting reading (a `memory_claims` row that carries one) for 'reading-place' and
    -- 'reading-no'; for 'reading-undo' it is the answer of the keep it undoes. The bot makes the
    -- very calls the DM screen would (`ClaimStore.place` / `reject`, the store's Undo), so
    -- consent, the plan and every rule are checked there, again.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}, {_READING_LIST}));
    """
    + _reading_door
    + r"""
    -- The answer door says when the action was made and when it was last picked up, so the page
    -- can show how long it has waited and whether it moved (CLAUDE.md: every wait shows it).
    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id, 'resultOther', r.result_other,
                                  'createdAt', r.created_at, 'claimedAt', r.claimed_at,
                                  'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;

    -- The readings that wait for the DM: claims that carry a reading, not yet kept or turned
    -- down. DM only (anyone else gets NULL). The words are the claim's own (names and a
    -- relationship, as the waiting list shows them), never a transcript line.
    CREATE OR REPLACE FUNCTION dmbot_web_readings(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'total', (SELECT count(*) FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.reading IS NOT NULL AND k.how <> 'plan'
                        AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'),
            'readings', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', m.id, 'subject', m.subject, 'relationship', m.relationship,
                    'object', m.object, 'details', m.details,
                    'at', m.session_started_at,
                    'view', m.reading->>'view', 'status', m.reading->>'status',
                    'confidence', m.reading->'confidence',
                    'waitingOn', to_jsonb(ARRAY(
                        SELECT e.name FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'proposed'
                          AND e.type <> 'person'
                          AND lower(e.name) IN (lower(m.subject), lower(m.object)))))
                    ORDER BY m.session_started_at NULLS LAST, m.created_at, m.id)
                FROM (SELECT * FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.reading IS NOT NULL AND k.how <> 'plan'
                        AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'
                      ORDER BY k.session_started_at NULLS LAST, k.created_at, k.id
                      LIMIT 100) m), '[]'::jsonb));
    END
    $fn$;
    -- (redefined functions lose their pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_readings(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_readings(BIGINT, TEXT) FROM PUBLIC;
    """
)


# Meaning search (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"). `memory_vectors`: one row for
# each piece of the campaign's own text turned into numbers by Voyage AI: the owner's kind and id,
# the model that made them, a hash of the text (so a changed text is made again) and the numbers as
# a plain list, compared in Python inside one campaign (no extension needed). Never in a backup:
# they are made again. A transcript line's row is tied to the line itself, so EVERY way a line goes
# (Stop recording me, Delete my transcripts, retention, a deleted session or campaign) takes its
# numbers in the same statement: no vector of a person's words outlives the words.
MEMORY_VECTORS = """
    CREATE TABLE memory_vectors (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        owner_kind  TEXT NOT NULL CHECK (char_length(owner_kind) BETWEEN 1 AND 40),
        owner_id    TEXT NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 200),
        line_id     BIGINT,
        model       TEXT NOT NULL CHECK (char_length(model) BETWEEN 1 AND 100),
        text_hash   TEXT NOT NULL CHECK (char_length(text_hash) BETWEEN 1 AND 100),
        vector      REAL[] NOT NULL CHECK (cardinality(vector) BETWEEN 1 AND 4096),
        made_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, owner_kind, owner_id),
        -- a line's numbers are tied to the line; no other kind has a line
        CHECK ((owner_kind = 'line') = (line_id IS NOT NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        -- the line's own key only: a second unique index on a table that grows with every spoken
        -- line would cost the recorder on every insert. `save_lines` takes the line and its guild
        -- from the line's own row, so the two cannot differ.
        FOREIGN KEY (line_id) REFERENCES transcript_lines (id) ON DELETE CASCADE
    );
    -- (deleting a line finds its numbers by index, not a scan)
    CREATE INDEX memory_vectors_by_line ON memory_vectors (line_id) WHERE line_id IS NOT NULL;
    """ + _isolate("memory_vectors")


# What the name pass did to one session (#2684, #2628), kept so the website can show it and undo it
# (it was only ever posted to Discord, and an Undo deletes the lines that said a fix was made).
# `name_pass_reports`: one row for the session, the counts the DM is told (words left as heard,
# names offered as new, words left alone near a secret name) and whether PlotBot's answer was cut.
# `name_pass_fixes`: one row for each name fixed, with the Undo number, the list name and how many
# times, and when it was undone. Counts and the campaign's own list names only: the spellings as
# heard stay in `transcript_respells` (read from there while the fix stands, so they are never
# copied and go with the lines they came from). Deleted with the session; its keys follow a campaign
# that moves to another server.
NAME_PASS_REPORTS = (
    """
    CREATE TABLE name_pass_reports (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        unsure      INTEGER NOT NULL DEFAULT 0 CHECK (unsure >= 0),
        new_names   INTEGER NOT NULL DEFAULT 0 CHECK (new_names >= 0),
        guarded     INTEGER NOT NULL DEFAULT 0 CHECK (guarded >= 0),
        cut         BOOLEAN NOT NULL DEFAULT false,
        made_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE TABLE name_pass_fixes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        batch       BIGINT NOT NULL,
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
        times       INTEGER NOT NULL CHECK (times >= 0),
        made_at     BIGINT NOT NULL,
        undone_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_id, batch),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (an Undo knows its batch and campaign, not the session)
    CREATE INDEX name_pass_fixes_by_batch ON name_pass_fixes (guild_id, campaign_id, batch);
    -- (the foreign keys to a session: deleting a session finds its rows by index, not a scan;
    -- the reports' primary key leads with the campaign, so it does not serve that)
    CREATE INDEX name_pass_reports_by_session ON name_pass_reports (session_id, guild_id);
    CREATE INDEX name_pass_fixes_by_session ON name_pass_fixes (session_id, guild_id);
    """
    + _isolate("name_pass_reports")
    + _isolate("name_pass_fixes")
)


# The recap page's "Names fixed this session" (#2684): what the name pass did to the recap's
# session, read for a DM of the campaign only (anyone else gets NULL). The counts and each fix come
# from `name_pass_reports` and `name_pass_fixes`; the spellings as heard are read from
# `transcript_respells` while a fix stands (a fix that was undone has none) and are never copied.
# They are words from a transcript, for the campaign's DM and no one else: the API never logs them.
# A new function: no row changes.
NAMES_FIXED_DOOR = r"""
    CREATE FUNCTION dmbot_recap_names_fixed(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE sid TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT s.id INTO sid FROM transcript_sessions s
         WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
         ORDER BY s.id LIMIT 1;
        RETURN jsonb_build_object(
            'fixed', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', f.batch::text, 'name', f.name, 'times', f.times,
                    'forms', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('heard', x.form, 'times', x.n)
                                         ORDER BY x.n DESC, x.form)
                        FROM (SELECT form, count(*) AS n
                              FROM transcript_respells r, unnest(r.was) AS form
                              WHERE r.guild_id = g AND r.campaign_id = c
                                AND r.session_id = f.session_id AND r.batch = f.batch
                              GROUP BY form ORDER BY count(*) DESC, form LIMIT 12) x
                    ), '[]'::jsonb),
                    'undone', f.undone_at IS NOT NULL)
                    ORDER BY f.times DESC, f.name, f.batch)
                FROM name_pass_fixes f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.session_id = sid), '[]'::jsonb),
            'unsure', COALESCE((SELECT p.unsure FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0),
            'new', COALESCE((SELECT p.new_names FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0),
            'guarded', COALESCE((SELECT p.guarded FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""


# The recap page's Undo of a name-pass fix (#2684): one more action on the website's queue, for the
# bot to apply through the very calls the Discord Undo makes. `target_id` is the fix's Undo number
# (`name_pass_fixes.batch`, as text). The door says no unless that fix is the campaign's own; one
# already undone is queued and answered `gone` by the bot (nothing is changed).
NAMES_FIXED_ACTIONS = ("names-fixed-undo",)
_NAMES_FIXED_LIST = ", ".join(f"'{a}'" for a in NAMES_FIXED_ACTIONS)
_names_fixed_door = _reading_door
_all_readings = ", ".join(f"'{a}'" for a in READING_ACTIONS)
if _names_fixed_door.count(_all_readings) != 5:
    raise RuntimeError("the name-action door no longer lists the reading actions five times")
_names_fixed_door = _names_fixed_door.replace(
    _all_readings, f"{_all_readings}, {_NAMES_FIXED_LIST}"
)
for _old, _new in (
    (
        # (a fix is named by its Undo number, a decimal; every other target is a 32-hex id)
        "AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))\n           OR (act NOT IN (",
        "AND (tgt IS NULL OR tgt !~ CASE WHEN act = 'names-fixed-undo' THEN '^[0-9]{1,12}$'\n"
        "                                             ELSE '^[0-9a-f]{32}$' END))\n"
        "           OR (act NOT IN (",
    ),
    (
        "act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%' THEN",
        "act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%'\n"
        "           OR act LIKE 'names-fixed-%' THEN",
    ),
    (
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
        "            ELSIF act = 'names-fixed-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_pass_fixes f\n"
        "                   WHERE f.guild_id = g AND f.campaign_id = c\n"
        "                   AND f.batch::text = tgt) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
    ),
):
    if _names_fixed_door.count(_old) != 1:  # (an explicit raise: `python -O` drops an assert)
        raise RuntimeError(
            f"the name-action door changed under the names-fixed migration: {_old!r}"
        )
    _names_fixed_door = _names_fixed_door.replace(_old, _new)

NAMES_FIXED_UNDO = (
    f"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}, {_READING_LIST}, {_NAMES_FIXED_LIST}));
    """
    + _names_fixed_door
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    """
)


# ✏️ Change the words on the recap page's names to check (#2593; Discord's is #2570). One more answer
# for `name_answers` (`change_words`: `text` holds the whole name and `target_id` how it was heard,
# at most the name limit each), the answer door taking it, and, from the read door, the lines of
# table speech that say each name (only people who agree at that moment), for the website to work
# the likely longer names out from when the page is read: nothing is stored, so a speaker who
# stops being recorded drops out at once. Both doors are rebuilt from their current text by guarded
# replacement, so a drift in either makes this fail loudly rather than quietly change a rule.
def _swap(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    for old, new in pairs:
        if text.count(old) != 1:  # (an explicit raise: `python -O` drops an assert)
            raise RuntimeError(f"the names-to-check door no longer has: {old[:50]}")
        text = text.replace(old, new, 1)
    return text


_CHECK_ANSWER_DOOR = _swap(
    NAME_ANSWERS[
        NAME_ANSWERS.index("    CREATE FUNCTION dmbot_name_answer_add") : NAME_ANSWERS.index(
            "    DO $do$"
        )
    ].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
    (
        (
            "'respell', 'not_a_name', 'explain')",
            "'respell', 'not_a_name', 'explain', 'change_words')",
        ),
        (
            "OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)",
            "OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)\n"
            "           OR (act = 'change_words' AND char_length(clean) NOT BETWEEN 1 AND 100)",
        ),
        (
            "OR (act <> 'same' AND COALESCE(tgt, '') <> '') THEN",
            "OR (act NOT IN ('same', 'change_words') AND COALESCE(tgt, '') <> '')\n"
            "           OR (act = 'change_words' AND (char_length(COALESCE(tgt, '')) > 100\n"
            "                OR COALESCE(tgt, '') ~ '[[:cntrl:]]')) THEN",
        ),
    ),
)
_CHECK_READ_DOOR = _swap(
    NAME_GATE[
        NAME_GATE.index("    CREATE OR REPLACE FUNCTION dmbot_recap_names_read") : NAME_GATE.index(
            "    DO $do$"
        )
    ],
    (
        (
            "'note', e.description,",
            "'note', e.description, 'lines', COALESCE((\n"
            "    SELECT jsonb_agg(q.t ORDER BY q.o) FROM (\n"
            "      SELECT COALESCE(l.text, l.heard) AS t,\n"
            "             row_number() OVER (ORDER BY m.session_started_at NULLS LAST,\n"
            "                                m.created_at, m.id) AS o\n"
            "      FROM memory_mentions m\n"
            "      JOIN transcript_lines l\n"
            "        ON l.guild_id = g\n"
            "       AND l.session_id = substring(m.line_ref from '^(.*):[0-9]+:[0-9]+$')\n"
            "       AND l.started_ms::TEXT = substring(m.line_ref from ':([0-9]+):[0-9]+$')\n"
            "       AND l.user_id::TEXT = substring(m.line_ref from ':([0-9]+)$')\n"
            "      WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = e.id\n"
            "        AND l.sidebar IS NULL\n"
            "        AND EXISTS (SELECT 1 FROM consent n\n"
            "                    WHERE n.guild_id = g AND n.user_id = l.user_id)\n"
            "      ORDER BY 2 LIMIT 8) q), '[]'::jsonb),",
        ),
    ),
)
NAME_CHANGE_WORDS = (
    """
    ALTER TABLE name_answers DROP CONSTRAINT name_answers_action_check;
    ALTER TABLE name_answers ADD CONSTRAINT name_answers_action_check CHECK (action IN
        ('confirm', 'same', 'respell', 'not_a_name', 'explain', 'change_words'));
    ALTER TABLE name_answers DROP CONSTRAINT name_answers_target_id_check;
    ALTER TABLE name_answers ADD CONSTRAINT name_answers_target_id_check
        CHECK (char_length(target_id) <= 100);
    """
    + _CHECK_READ_DOOR
    + _CHECK_ANSWER_DOOR
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_add('
            'BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """
)

# The recap page's "Present: ..." for each scene (#2684, #2577): the DM's read of who was in each
# scene and of the campaign's player characters they may pick from, and the save of one scene's
# list. Names only, never a transcript word; for a DM of the campaign alone (the read gives NULL
# to anyone else, the save says `not_found`). The list is kept on the beat with `saved: true`, so
# nothing works it out again; "Make it again" clears the beats, and so the lists (the page says
# so first). A new function pair: no row changes.
RECAP_PRESENT_DOORS = r"""
    -- (A scene's number `n` is its place in the stored sheet's array, as the page reads it back.
    -- DMbot writes the sheet as objects only; anything else is skipped here and refused below.
    -- Two characters of one campaign with the same name are listed once.)
    CREATE FUNCTION dmbot_recap_present(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE sheet JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT r.beats INTO sheet FROM recap_jobs r
         WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'beats', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'n', t.ord,
                    'present', CASE WHEN jsonb_typeof(t.b -> 'present') = 'array'
                                    THEN t.b -> 'present' ELSE '[]'::jsonb END,
                    'saved', COALESCE((t.b -> 'saved') = 'true'::jsonb, false))
                    ORDER BY t.ord)
                FROM jsonb_array_elements(
                         CASE WHEN jsonb_typeof(sheet) = 'array' THEN sheet ELSE '[]'::jsonb END
                     ) WITH ORDINALITY AS t(b, ord)
                WHERE jsonb_typeof(t.b) = 'object'), '[]'::jsonb),
            'characters', COALESCE((
                SELECT jsonb_agg(DISTINCT e.name) FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.role = 'player_character'
                  AND e.played_by IS NOT NULL AND e.status = 'confirmed'), '[]'::jsonb));
    END
    $fn$;
    -- Save scene `n`'s list (its place in the sheet, as the read numbers it). Gives {"n",
    -- "present" (the campaign's order), "saved": true} or {"error": not_found | not_allowed |
    -- stale | bad_present}. Only a recap that is ready and still a draft; the DM's word wins, so
    -- a list once saved is replaced only by the DM.
    CREATE FUNCTION dmbot_recap_set_present(g BIGINT, c TEXT, at_ BIGINT, n INT, names JSONB)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        chosen TEXT[];
        asked INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN jsonb_build_object('error', 'not_found');
        END IF;
        SELECT * INTO job FROM recap_jobs
         WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('error', 'not_found');
        END IF;
        IF job.state <> 'ready' OR job.status <> 'draft' THEN
            RETURN jsonb_build_object('error', 'not_allowed');
        END IF;
        IF jsonb_typeof(job.beats) IS DISTINCT FROM 'array' OR n < 1
           OR n > jsonb_array_length(job.beats)
           OR jsonb_typeof(job.beats -> (n - 1)) IS DISTINCT FROM 'object' THEN
            RETURN jsonb_build_object('error', 'stale');
        END IF;
        IF jsonb_typeof(names) IS DISTINCT FROM 'array' OR jsonb_array_length(names) > 12
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(names) x
                      WHERE jsonb_typeof(x) IS DISTINCT FROM 'string') THEN
            RETURN jsonb_build_object('error', 'bad_present');
        END IF;
        SELECT count(DISTINCT x) INTO asked FROM jsonb_array_elements_text(names) x;
        SELECT COALESCE(array_agg(DISTINCT e.name), '{}') INTO chosen
          FROM memory_entities e
         WHERE e.guild_id = g AND e.campaign_id = c AND e.role = 'player_character'
           AND e.played_by IS NOT NULL AND e.status = 'confirmed'
           AND e.name IN (SELECT jsonb_array_elements_text(names));
        IF asked <> jsonb_array_length(names) OR cardinality(chosen) <> asked THEN
            RETURN jsonb_build_object('error', 'bad_present');
        END IF;
        UPDATE recap_jobs SET beats = (
            SELECT jsonb_agg(CASE WHEN t.ord = n
                                  THEN t.b || jsonb_build_object('present', to_jsonb(chosen),
                                                                 'saved', true)
                                  ELSE t.b END ORDER BY t.ord)
            FROM jsonb_array_elements(job.beats) WITH ORDINALITY AS t(b, ord))
         WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN jsonb_build_object('n', n, 'present', to_jsonb(chosen), 'saved', true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_present(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_present(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)
        FROM PUBLIC;
"""

RECAP_NAME_PLACES = r"""
    -- dmbot_recap_name_places: where each waiting name of a recap was said (#2403), for the
    -- campaign's DMs only. Per name, up to two windows (the earliest place and, with more than two,
    -- the middle one, as Discord's card picks them) of up to three table lines around the line the
    -- name was heard in, and how many places are left unshown. Nothing is copied: the words are
    -- read from transcript_lines now, and a line is kept only if its speaker has a consent row for
    -- this server at this moment, so someone who pressed "Stop recording me" is gone from the very
    -- next read. Such a line is `{"removed": true}` (no speaker, time or words); a window with
    -- nothing left, or whose transcript was deleted, is `available: false` with no lines. DM
    -- sidebar lines are never read (they are DMbot's answers), and in an imported session only
    -- game talk is.
    CREATE FUNCTION dmbot_recap_name_places(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        result JSONB := '{}'::jsonb;
        ent RECORD;
        ptr RECORD;
        wins JSONB;
        found JSONB;
        total INT;
        shown INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        FOR ent IN
            SELECT e.id
            FROM recap_name_checks k
            JOIN memory_entities e
              ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id AND e.id = k.entity_id
            WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
              AND e.status = 'proposed'
              AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                              WHERE s.guild_id = g AND s.campaign_id = c
                                AND s.entity_id = e.id AND s.secret)
            ORDER BY e.id
        LOOP
            wins := '[]'::jsonb;
            shown := 0;
            SELECT count(*) INTO total FROM (
                SELECT DISTINCT m.line_ref FROM memory_mentions m
                JOIN transcript_sessions t
                  ON t.guild_id = g AND t.campaign_id = c
                 AND t.id = substring(m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$')
                WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = ent.id
                  AND m.line_ref ~ '^.*:[0-9]{1,18}:[0-9]{1,18}$') q;
            FOR ptr IN
                SELECT r.sid, r.ms, r.uid, t.started_at, t.transcripts_deleted_at AS gone,
                       (SELECT count(*) FROM transcript_sessions e
                         WHERE e.guild_id = g AND e.campaign_id = c
                           AND (e.line_count > 0 OR e.transcripts_deleted_at IS NOT NULL)
                           AND (e.started_at, e.id) <= (t.started_at, t.id)) AS number
                FROM (
                    SELECT q.*,
                           row_number() OVER (
                               ORDER BY q.at NULLS LAST, q.ms, q.sid, q.uid) AS rn,
                           count(*) OVER () AS cnt
                    FROM (
                        SELECT DISTINCT ON (m.line_ref)
                               substring(m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$') AS sid,
                               substring(m.line_ref
                                         from ':([0-9]{1,18}):[0-9]{1,18}$')::BIGINT AS ms,
                               substring(m.line_ref from ':([0-9]{1,18})$')::BIGINT AS uid,
                               m.session_started_at AS at
                        FROM memory_mentions m
                        WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = ent.id
                          AND m.line_ref ~ '^.*:[0-9]{1,18}:[0-9]{1,18}$'
                          AND EXISTS (SELECT 1 FROM transcript_sessions t0
                                      WHERE t0.guild_id = g AND t0.campaign_id = c
                                        AND t0.id = substring(
                                            m.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$'))
                        ORDER BY m.line_ref, m.created_at, m.id) q) r
                JOIN transcript_sessions t
                  ON t.guild_id = g AND t.campaign_id = c AND t.id = r.sid
                WHERE r.cnt <= 2 OR r.rn = 1 OR r.rn = r.cnt / 2 + 1
                ORDER BY r.rn
            LOOP
                shown := shown + 1;
                found := '[]'::jsonb;
                IF ptr.gone IS NULL THEN
                    SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'ms', w.started_ms, 'uid', w.user_id::TEXT, 'hit', w.hit,
                               'agreed', w.agreed,
                               -- (words and name only for a speaker who agrees: the rest never
                               -- leave the database)
                               'text', CASE WHEN w.agreed THEN w.said ELSE '' END,
                               'name', CASE WHEN w.agreed
                                       THEN COALESCE(t2.speaker_names ->> w.user_id::TEXT, '')
                                       ELSE '' END)
                               ORDER BY w.started_ms, w.id), '[]'::jsonb)
                    INTO found
                    FROM (
                        SELECT x.*, EXISTS (
                                   SELECT 1 FROM consent n
                                   WHERE n.guild_id = g AND n.user_id = x.user_id) AS agreed
                        FROM (
                            (SELECT l.id, l.started_ms, l.user_id, false AS hit,
                                    COALESCE(l.text, l.heard) AS said
                             FROM transcript_lines l, LATERAL (
                                SELECT p.started_ms, p.id FROM transcript_lines p
                                WHERE p.guild_id = g AND p.session_id = ptr.sid
                                  AND p.started_ms = ptr.ms AND p.user_id = ptr.uid
                                  AND p.sidebar IS NULL
                                ORDER BY p.id LIMIT 1) here
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND (l.started_ms, l.id) < (here.started_ms, here.id)
                             ORDER BY l.started_ms DESC, l.id DESC LIMIT 1)
                            UNION ALL
                            (SELECT l.id, l.started_ms, l.user_id, true,
                                    COALESCE(l.text, l.heard)
                             FROM transcript_lines l
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND l.started_ms = ptr.ms AND l.user_id = ptr.uid
                             ORDER BY l.id LIMIT 1)
                            UNION ALL
                            (SELECT l.id, l.started_ms, l.user_id, false,
                                    COALESCE(l.text, l.heard)
                             FROM transcript_lines l, LATERAL (
                                SELECT p.started_ms, p.id FROM transcript_lines p
                                WHERE p.guild_id = g AND p.session_id = ptr.sid
                                  AND p.started_ms = ptr.ms AND p.user_id = ptr.uid
                                  AND p.sidebar IS NULL
                                ORDER BY p.id LIMIT 1) here
                             WHERE l.guild_id = g AND l.session_id = ptr.sid AND l.sidebar IS NULL
                               AND (l.topic = 'game' OR NOT EXISTS (
                                    SELECT 1 FROM session_imports i
                                    WHERE i.guild_id = g AND i.session_id = ptr.sid))
                               AND (l.started_ms, l.id) > (here.started_ms, here.id)
                             ORDER BY l.started_ms, l.id LIMIT 1)) x) w
                    JOIN transcript_sessions t2 ON t2.guild_id = g AND t2.id = ptr.sid;
                END IF;
                wins := wins || jsonb_build_array(jsonb_build_object(
                    'session', ptr.number, 'startedAt', ptr.started_at, 'lines', found));
            END LOOP;
            result := result || jsonb_build_object(ent.id, jsonb_build_object(
                'windows', wins, 'more', GREATEST(total - shown, 0)));
        END LOOP;
        RETURN result;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_name_places(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_name_places(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""
# The lines the names-to-check door returns (0242) were picked by server only: a mention's line is
# now also required to be in one of THIS campaign's sessions, so nothing of another campaign's
# transcript can come through, even by a pointer that names it (#2729). The door is rebuilt from
# 0242's text by guarded replacement, with its pinned search_path and REVOKE kept.
_CAMPAIGN_READ_DOOR = _swap(
    _CHECK_READ_DOOR,
    (
        (
            "      JOIN transcript_lines l\n        ON l.guild_id = g\n",
            "      JOIN transcript_sessions s\n        ON s.guild_id = g AND s.campaign_id = c\n"
            "      JOIN transcript_lines l\n        ON l.guild_id = g AND l.session_id = s.id\n",
        ),
    ),
)
NAMES_READ_CAMPAIGN = (
    _CAMPAIGN_READ_DOOR
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    """
)

# One session's after-session report for the website (#2661; docs/STORY_MEMORY.md 9a): the DM's
# alone. Items are the minor contradictions and the differences PlotBot sorted as disguised,
# misunderstood or false, plus what the DM answered. An item belongs to a session when it was found
# in it (`session_started_at`); "That's intended", "Not a problem" and a fix are not raised again
# once answered before the session began, and "Remind me" comes back in the report of the session
# after the one it was answered in. The key an answer is kept under is the same hash as
# `memory/report_store.py` makes (sha256 of the item's kind and ids, 32 hex). Only ids, DMbot's own
# words (the connections' names and relationship) and numbers leave: evidence is its kind, its
# session and whether it still stands (a link whose words were removed has no target), never a
# line, a speaker or a quote. Items found but not sorted (the call limit stopped the run) are only
# counted. At most 200 of each list.
_REPORT_RELATION = """
        (SELECT jsonb_build_object(
            'id', r.id, 'subject', s.name, 'predicate', r.predicate, 'detail', r.detail,
            'directional', r.directional, 'object', o.name,
            'beliefAt', (SELECT b.session_at FROM memory_beliefs b
                         WHERE b.guild_id = g AND b.campaign_id = c
                           AND b.perspective_id = {persp} AND b.relation_id = r.id
                         ORDER BY b.seq DESC LIMIT 1),
            'evidence', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', e.kind, 'at', e.session_started_at,
                           'available', e.target_id IS NOT NULL) ORDER BY e.added_at, e.id)
                FROM (SELECT u.* FROM (
                          SELECT DISTINCT ON (x.kind, x.session_started_at, x.target_id) x.*
                          FROM (
                              SELECT e0.* FROM memory_evidence e0
                              WHERE e0.guild_id = g AND e0.campaign_id = c
                                AND e0.relation_id = r.id
                              UNION ALL
                              SELECT e1.* FROM memory_beliefs b2
                              JOIN memory_evidence e1
                                ON e1.guild_id = g AND e1.campaign_id = c
                               AND e1.belief_id = b2.id
                              WHERE b2.guild_id = g AND b2.campaign_id = c
                                AND b2.perspective_id = {persp} AND b2.relation_id = r.id) x
                          ORDER BY x.kind, x.session_started_at, x.target_id,
                                   x.added_at, x.id) u
                      ORDER BY u.added_at, u.id LIMIT 10) e), '[]'::jsonb))
         FROM memory_relations r
         JOIN memory_entities s ON s.guild_id = g AND s.campaign_id = c AND s.id = r.subject_id
         JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c AND o.id = r.object_id
         WHERE r.guild_id = g AND r.campaign_id = c AND r.id = {rel}
           AND (r.status = 'confirmed'
                OR (m.involves_guess AND r.status = 'proposed' AND r.secret
                    AND r.source = 'extractor')))"""
_REPORT_ANSWER = """
        CASE WHEN a.item_key IS NULL THEN NULL ELSE jsonb_build_object(
            'action', a.answer, 'at', a.answered_at, 'remindFrom', a.remind_from) END"""
_REPORT_WHO = """
        (SELECT jsonb_build_object('kind', p.kind, 'name', pe.name)
         FROM memory_perspectives p
         LEFT JOIN memory_entities pe
           ON pe.guild_id = g AND pe.campaign_id = c AND pe.id = p.entity_id
         WHERE p.guild_id = g AND p.campaign_id = c AND p.id = {persp})"""
# which items this session's report holds (the row is `m`, its answer `a`)
_REPORT_HERE = """
        ((m.session_started_at = at_
          AND NOT COALESCE(
                  a.answer IN ('intended', 'not_a_problem', 'fixed') AND a.answered_at < at_,
                  false))
         OR (a.answer = 'remind' AND m.session_started_at IS DISTINCT FROM at_
             AND at_ = (SELECT min(t.started_at) FROM transcript_sessions t
                        WHERE t.guild_id = g AND t.campaign_id = c
                          AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                          AND t.started_at > a.remind_from)))"""
_REPORT_KEY_C = (
    "left(encode(sha256(convert_to('contradiction|' || m.perspective_id || '|' || m.kind"
    " || '|' || m.first_relation_id || '|' || m.second_relation_id, 'UTF8')), 'hex'), 32)"
)
_REPORT_KEY_D = (
    "left(encode(sha256(convert_to('difference|' || m.perspective_id || '|' || m.relation_id,"
    " 'UTF8')), 'hex'), 32)"
)
_TRUTH = (
    "(SELECT tp.id FROM memory_perspectives tp"
    " WHERE tp.guild_id = g AND tp.campaign_id = c AND tp.kind = 'truth')"
)
REPORT_READ_DOOR = (
    r"""
    CREATE FUNCTION dmbot_web_report(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'now', extract(epoch FROM now())::BIGINT,
            'run', (SELECT jsonb_build_object(
                        'state', r.state, 'stage', r.stage, 'done', r.done, 'total', r.total,
                        'startedAt', r.started_at, 'updatedAt', r.updated_at, 'reason', r.reason)
                    FROM memory_report_runs r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_),
            'contradictions', COALESCE((
                SELECT jsonb_agg(x.item ORDER BY x.reminded DESC, x.found_at DESC, x.key)
                FROM (
                    SELECT """
    + _REPORT_KEY_C
    + r""" AS key, m.found_at,
                           COALESCE(a.answer = 'remind' AND a.remind_from < at_, false)
                               AS reminded,
                           jsonb_build_object(
                               'key', """
    + _REPORT_KEY_C
    + r""", 'kind', m.kind, 'reason', m.reason,
                               'timesUnknown', m.times_unknown,
                               'remindFrom', CASE WHEN a.answer = 'remind'
                                                       AND a.remind_from < at_
                                                  THEN a.remind_from END,
                               'who', """
    + _REPORT_WHO.format(persp="m.perspective_id")
    + r""",
                               'first', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.first_relation_id")
    + r""",
                               'second', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.second_relation_id")
    + r""",
                               'answer', """
    + _REPORT_ANSWER
    + r""") AS item
                    FROM memory_contradictions m
                    LEFT JOIN report_answers a
                      ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_C
    + r"""
                    WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'minor'
                      AND """
    + _REPORT_HERE
    + r"""
                    ORDER BY reminded DESC, m.found_at DESC, key
                    LIMIT 200) x), '[]'::jsonb),
            'differences', COALESCE((
                SELECT jsonb_agg(x.item ORDER BY x.reminded DESC, x.found_at DESC, x.key)
                FROM (
                    SELECT """
    + _REPORT_KEY_D
    + r""" AS key, m.found_at,
                           COALESCE(a.answer = 'remind' AND a.remind_from < at_, false)
                               AS reminded,
                           jsonb_build_object(
                               'key', """
    + _REPORT_KEY_D
    + r""", 'sort', m.sort, 'kind', m.kind, 'reason', m.reason,
                               'timesUnknown', m.times_unknown,
                               'remindFrom', CASE WHEN a.answer = 'remind'
                                                       AND a.remind_from < at_
                                                  THEN a.remind_from END,
                               'who', """
    + _REPORT_WHO.format(persp="m.perspective_id")
    + r""",
                               'believes', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.relation_id")
    + r""",
                               'truth', CASE WHEN m.truth_relation_id IS NULL THEN NULL ELSE """
    + _REPORT_RELATION.format(persp=_TRUTH, rel="m.truth_relation_id")
    + r""" END,
                               'open', (SELECT jsonb_build_object('a', r2.subject_id,
                                                                 'b', r2.object_id)
                                        FROM memory_relations r2
                                        WHERE r2.guild_id = g AND r2.campaign_id = c
                                          AND r2.id = m.relation_id
                                          AND (r2.status = 'confirmed'
                                               OR (m.involves_guess AND r2.status = 'proposed'
                                                   AND r2.secret
                                                   AND r2.source = 'extractor'))),
                               'answer', """
    + _REPORT_ANSWER
    + r""") AS item
                    FROM memory_differences m
                    LEFT JOIN report_answers a
                      ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_D
    + r"""
                    WHERE m.guild_id = g AND m.campaign_id = c
                      AND m.sort IN ('disguised', 'misunderstood', 'false')
                      AND """
    + _REPORT_HERE
    + r"""
                    ORDER BY reminded DESC, m.found_at DESC, key
                    LIMIT 200) x), '[]'::jsonb),
            'notSorted', (
                (SELECT count(*) FROM memory_differences m
                 WHERE m.guild_id = g AND m.campaign_id = c AND m.sort = 'not_sorted'
                   AND m.session_started_at = at_)
                + (SELECT count(*) FROM memory_contradictions m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'not_graded'
                     AND m.session_started_at = at_)));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    """
)

# The DM's answer to one item of a session's after-session report (#2661, slice 4b): a direct
# door, not the bot's queue (Supervisor, 2026-10-27), because an answer is the DM's bookkeeping and
# changes nothing in the story. It records "intended", "not a problem" or "remind me" in
# `report_answers`, or deletes the row for Undo, and answers at once. Inside itself it checks the
# item is in THAT session's report of THIS campaign (the same rule the read door lists by), so a
# key from another session, campaign or server is "gone"; an item that already has a final answer
# is "gone" too (a second press changes nothing), and "remind me" can be answered again. "fix"
# writes nothing: the page opens the Story graph, and recording "fixed" is for when the connection
# changes.
REPORT_ANSWER_DOOR = (
    r"""
    CREATE FUNCTION dmbot_web_report_answer(g BIGINT, c TEXT, at_ BIGINT, k TEXT, act TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        kind_ TEXT;
        had report_answers%ROWTYPE;
        wrote INT;
        now_ BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF k !~ '^[0-9a-f]{32}$'
           OR act NOT IN ('intended', 'not_a_problem', 'remind', 'fix', 'undo') THEN
            RETURN jsonb_build_object('status', 'bad');
        END IF;
        IF EXISTS (SELECT 1 FROM memory_contradictions m
                   LEFT JOIN report_answers a
                     ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_C
    + r"""
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'minor'
                     AND """
    + _REPORT_KEY_C
    + r""" = k
                     AND """
    + _REPORT_HERE
    + r""") THEN
            kind_ := 'contradiction';
        ELSIF EXISTS (SELECT 1 FROM memory_differences m
                      LEFT JOIN report_answers a
                        ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_D
    + r"""
                      WHERE m.guild_id = g AND m.campaign_id = c
                        AND m.sort IN ('disguised', 'misunderstood', 'false')
                        AND """
    + _REPORT_KEY_D
    + r""" = k
                        AND """
    + _REPORT_HERE
    + r""") THEN
            kind_ := 'difference';
        ELSE
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        SELECT * INTO had FROM report_answers
            WHERE guild_id = g AND campaign_id = c AND item_key = k;
        IF act = 'undo' THEN
            IF had.item_key IS NULL THEN
                RETURN jsonb_build_object('status', 'gone');
            END IF;
            DELETE FROM report_answers WHERE guild_id = g AND campaign_id = c AND item_key = k;
            RETURN jsonb_build_object('status', 'ok', 'answer', NULL);
        END IF;
        IF act = 'fix' THEN
            RETURN jsonb_build_object('status', 'ok', 'answer', CASE WHEN had.item_key IS NULL
                THEN NULL ELSE jsonb_build_object('action', had.answer, 'at', had.answered_at) END);
        END IF;
        -- One atomic write: a first answer inserts, a "remind me" may be replaced, and anything
        -- else (a final answer another press or another DM just made) writes nothing. Two
        -- presses at once cannot both win: the second one's conflict finds a final answer.
        INSERT INTO report_answers AS r (guild_id, campaign_id, item_key, item_kind, answer,
                                         answered_by, answered_at, remind_from)
        VALUES (g, c, k, kind_, act, dmbot_current_user(), now_,
                CASE WHEN act = 'remind' THEN at_ END)
        ON CONFLICT (guild_id, campaign_id, item_key) DO UPDATE SET
            item_kind = EXCLUDED.item_kind, answer = EXCLUDED.answer,
            answered_by = EXCLUDED.answered_by, answered_at = EXCLUDED.answered_at,
            remind_from = EXCLUDED.remind_from
        WHERE r.answer = 'remind';
        GET DIAGNOSTICS wrote = ROW_COUNT;
        IF wrote = 0 THEN
            RETURN jsonb_build_object('status', 'gone');  -- already answered: a second press
        END IF;
        RETURN jsonb_build_object('status', 'ok',
                                  'answer', jsonb_build_object('action', act, 'at', now_));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

RULE_SHARING_READ = r"""
    -- What a campaign shares and follows, and the sets other campaigns share in its server, for the
    -- website's House rules page (#2783; docs/PLAN.md, "Shared rulesets"). Read only, and for a DM
    -- of the campaign alone (NULL for anyone else), in a server on the session's list. It tells
    -- the page whether a share code is on, never the code (the page does not show codes: they are
    -- read in Discord, #2460), and for the sets of other campaigns only their name, their
    -- publisher campaign's name, the version and how many rules: none of a rule's words, who
    -- wrote it or anything else of those campaigns. Only rules cross, and these are numbers.
    CREATE FUNCTION dmbot_web_rule_sharing(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        mine JSONB;
        followed JSONB;
        here JSONB;
        n_rules BIGINT;
        owner_id BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT jsonb_build_object(
                   'name', s.name, 'shared', s.status = 'shared',
                   'version', (SELECT max(v.version) FROM rule_set_versions v
                               WHERE v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                                 AND v.set_id = s.id),
                   'hasCode', EXISTS (SELECT 1 FROM rule_set_codes k
                                      WHERE k.guild_id = s.guild_id
                                        AND k.campaign_id = s.campaign_id
                                        AND k.set_id = s.id AND k.active))
            INTO mine
            FROM rule_sets s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.kind = 'house_rules';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'name', f.set_name, 'publisher', f.publisher, 'version', f.version,
                   'updating', f.updating) ORDER BY f.position, f.followed_at, f.set_id),
                   '[]'::jsonb)
            INTO followed
            FROM rule_set_follows f
            WHERE f.guild_id = g AND f.campaign_id = c AND f.kind = 'house_rules';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'setId', s.id, 'name', s.name, 'publisher', p.name, 'version', v.version,
                   'rules', COALESCE(jsonb_array_length(v.content -> 'rules'), 0))
                   ORDER BY lower(s.name), s.id), '[]'::jsonb)
            INTO here
            FROM rule_sets s
            JOIN campaigns p ON p.guild_id = s.guild_id AND p.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
                AND v.version = (SELECT max(m.version) FROM rule_set_versions m
                                 WHERE m.guild_id = s.guild_id AND m.campaign_id = s.campaign_id
                                   AND m.set_id = s.id)
            WHERE s.guild_id = g AND s.status = 'shared' AND s.kind = 'house_rules'
              AND s.campaign_id <> c;
        SELECT count(*) INTO n_rules FROM house_rules r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.from_set IS NULL;
        SELECT k.owner_user_id INTO owner_id FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        -- The owner goes to the API only to judge the plan; the page is told yes or no.
        RETURN jsonb_build_object('published', mine, 'following', followed, 'here', here,
                                  'ruleCount', n_rules, 'owner', owner_id::TEXT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_sharing(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_sharing(BIGINT, TEXT) FROM PUBLIC;
"""

WEB_RULE_ACTIONS = (
    r"""
    -- What a DM chose on the website's House rules page about sharing (#2783), waiting for the
    -- bot: publish the campaign's rules as a set, stop sharing, look at a set by its share code,
    -- follow a set shared in the server or by code, stop following. The website has no right on
    -- the rule tables, so its door only checks the signed-in DM and queues the choice here; the
    -- bot applies it with the same store calls as Discord's buttons (the plan, the caps, the
    -- guess limit and who asked are checked again then). Share codes are never made, turned off
    -- or shown from here: they stay in Discord (#2460). `arg` is the set's name (1 to 60
    -- characters, `publish`), a set's id (`follow_here`) or a share code (`look_code`,
    -- `follow_code`), checked for its shape and nothing else. `note` is what the DM sees after,
    -- in Discord's own words (for `look_code`: the set's name, publisher and how many rules would
    -- be copied, never a rule's words). A row lives a day at most, is gone with its campaign,
    -- left out of backups, isolated per server and read back only by the DM who made it.
    CREATE TABLE web_rule_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN ('publish', 'stop_sharing', 'look_code',
                                                    'follow_here', 'follow_code', 'unfollow')),
        arg         TEXT NOT NULL DEFAULT '' CHECK (char_length(arg) <= 64),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'sharing', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 1000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page (which reads the stage) and the
        -- answer (which comes with `done` or `failed`) can never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'sharing'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE INDEX web_rule_actions_open ON web_rule_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_rule_actions")
    + r"""
    -- Queue one choice. The door checks what it can see: the caller is a DM of the campaign, the
    -- action is one Discord offers, its argument has the right shape, and the campaign's queue is
    -- not full. A second press of the same action and argument while one is open gets that one
    -- back. Answers 'ok' with the id, or 'bad_action', 'bad_arg' or 'too_soon'; NULL for anyone
    -- who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_rule_action_add(g BIGINT, c TEXT, act TEXT, a TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        want TEXT := COALESCE(a, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('publish', 'stop_sharing', 'look_code', 'follow_here',
                                      'follow_code', 'unfollow') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF (act IN ('stop_sharing', 'unfollow') AND want <> '')
           OR (act = 'publish' AND char_length(btrim(want)) NOT BETWEEN 1 AND 60)
           OR (act IN ('look_code', 'follow_code') AND want !~ '^[A-Z2-7]{10}$')
           OR (act = 'follow_here' AND want !~ '^[0-9a-f]{32}$') THEN
            RETURN jsonb_build_object('status', 'bad_arg');
        END IF;
        IF act = 'publish' THEN
            want := btrim(want);  -- the name is stored as it was checked, and as the store keeps it
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('rule_action:' || g::TEXT || c, 0));
        DELETE FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting, so
        -- it is not stuck behind itself for a day; the bot's own retake does the same. (The page
        -- calls it stalled from five minutes, `progress.STALL_AFTER_S`, so the DM hears of it
        -- before it is put back.) The share codes in `arg` are blanked by the bot as it finishes
        -- a row (#2793); the row itself stays a day.
        UPDATE web_rule_actions r SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.user_id = dmbot_current_user()
              AND r.action = act AND r.arg = want AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_rule_actions (guild_id, campaign_id, id, user_id, action, arg,
                                      created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the DM who made it (nobody else's): the state, the stage and
    -- times for the shared progress shape, the plain error word, and the note to show. NULL when
    -- there is no such choice of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_rule_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_rule_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM web_rule_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user()
              AND a.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'action', r.action, 'note', r.note, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_rule_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

WEB_SHEET_ACTIONS = (
    r"""
    -- A player's character-sheet choice on the website (#2816; the parity row of #2554), waiting
    -- for the bot: link a D&D Beyond sheet, forget it (the player's own), or a DM's read again of
    -- the campaign's sheets. The website never fetches a link: the bot does, with the same store
    -- calls as Discord's buttons, and checks again who plays the character, that they are still in
    -- the server, and the cooldowns. `url` is a character link of the one shape D&D Beyond uses,
    -- checked by the door before anything is queued. A row lives a day, is gone with its campaign,
    -- and is read back only by the person who made it.
    CREATE TABLE web_sheet_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        entity_id   TEXT NOT NULL DEFAULT '' CHECK (entity_id ~ '^([0-9a-f]{32})?$'),
        action      TEXT NOT NULL CHECK (action IN ('link', 'forget', 'read_again')),
        url         TEXT NOT NULL DEFAULT '' CHECK (char_length(url) <= 80),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page and the answer never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'reading'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE INDEX web_sheet_actions_open ON web_sheet_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_sheet_actions")
    + r"""
    -- What the signed-in person may see of a campaign's character sheets: a DM sees every sheet
    -- (read only), a player only the sheets of the characters they play. The summary is the
    -- allow-listed snapshot's names and numbers, read here field by field. NULL for a server the
    -- person is not in, or someone who is neither a DM nor a player in this campaign.
    CREATE FUNCTION dmbot_web_sheet_read(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        plays BOOLEAN;
        found_rows JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        plays := EXISTS (SELECT 1 FROM memory_entities e
                         WHERE e.guild_id = g AND e.campaign_id = c
                           AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                           AND e.status NOT IN ('merged', 'rejected'));
        IF NOT is_dm AND NOT plays THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'entityId', s.entity_id, 'name', e.name,
            'mine', e.played_by = dmbot_current_user(),
            'source', s.source, 'url', s.url, 'fetchedAt', s.fetched_at,
            'summary', CASE WHEN s.sheet IS NULL THEN NULL ELSE jsonb_build_object(
                'species', s.sheet->'species', 'level', s.sheet->'level',
                'classes', s.sheet->'classes', 'maxHp', s.sheet->'max_hp',
                'ac', s.sheet->'ac') END
        ) ORDER BY lower(e.name), s.entity_id), '[]'::jsonb) INTO found_rows
            FROM character_sheets s JOIN memory_entities e
              ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id AND e.id = s.entity_id
            WHERE s.guild_id = g AND s.campaign_id = c
              AND e.status NOT IN ('merged', 'rejected') AND e.played_by IS NOT NULL
              AND e.type <> 'person'
              -- Only the sheet of whoever plays the character now, never a previous player's.
              AND s.player_id = e.played_by
              AND (is_dm OR e.played_by = dmbot_current_user());
        RETURN jsonb_build_object('isDm', is_dm, 'sheets', found_rows);
    END
    $fn$;

    -- Queue one choice. `act` is link (the player's own character, a D&D Beyond character link
    -- in `link`), forget (the player's own sheet) or read_again (a DM, the campaign's sheets, once
    -- a minute). Answers 'ok' with the id; a second press of the same choice while one is open
    -- gets that one back. 'not_found' for a character or sheet that isn't there, 'not_allowed'
    -- when the caller isn't the one who plays it (or a DM, for read_again), 'bad_link' for a link
    -- of any other shape, 'too_soon' inside the link cooldown (20 seconds a person),
    -- 'read_too_soon' inside the read_again cooldown (a minute a campaign) and 'queue_full' when
    -- the queue is full. A link for another URL than an open one is queued on its own. NULL for
    -- a server the person is
    -- not in.
    CREATE FUNCTION dmbot_web_sheet_action_add(g BIGINT, c TEXT, ent TEXT, act TEXT, lnk TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        is_dm BOOLEAN;
        mine BOOLEAN;
        want TEXT := COALESCE(lnk, '');
        who TEXT := COALESCE(ent, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF act IS NULL OR act NOT IN ('link', 'forget', 'read_again') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF act = 'read_again' THEN
            IF NOT is_dm THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            who := '';
            want := '';
        ELSE
            IF who !~ '^[0-9a-f]{32}$' THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            mine := EXISTS (SELECT 1 FROM memory_entities e
                            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = who
                              AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                              AND e.status NOT IN ('merged', 'rejected'));
            IF NOT mine THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            IF act = 'link'
               AND want !~ '^https://www[.]dndbeyond[.]com/characters/[0-9]{1,12}$' THEN
                RETURN jsonb_build_object('status', 'bad_link');
            END IF;
            IF act = 'forget' THEN
                want := '';
                IF NOT EXISTS (SELECT 1 FROM character_sheets s
                               WHERE s.guild_id = g AND s.campaign_id = c
                                 AND s.entity_id = who AND s.player_id = dmbot_current_user()) THEN
                    RETURN jsonb_build_object('status', 'not_found');
                END IF;
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('sheet_action:' || g::TEXT || c, 0));
        DELETE FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE web_sheet_actions a SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state = 'working'
              AND a.updated_at < now_s - 600;
        SELECT a.id INTO have FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.entity_id = who AND a.action = act AND a.url = want
              AND a.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF act = 'link' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.action = 'link' AND a.created_at > now_s - 20) THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        IF act = 'read_again' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.action = 'read_again'
              AND a.created_at > now_s - 60) THEN
            RETURN jsonb_build_object('status', 'read_too_soon');
        END IF;
        IF (SELECT count(*) FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 20
        THEN
            RETURN jsonb_build_object('status', 'queue_full');
        END IF;
        INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, entity_id, action, url,
                                       created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), who, act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one of the caller's own choices: the state and stage for the shared progress
    -- shape, the plain error word, and the character it was about. NULL when there is no such
    -- choice of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_sheet_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_sheet_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user()
              AND a.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'action', r.action, 'entityId', r.entity_id, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

REPORT_FIXED_REMIND = """
    -- A `fixed` answer (#2661) replaces a "remind me": the date goes here, so Undoing the change
    -- that fixed the item puts the reminder back as it was.
    ALTER TABLE report_answers ADD COLUMN remind_was BIGINT;
    ALTER TABLE report_answers ADD CONSTRAINT report_answers_remind_was
        CHECK (remind_was IS NULL OR answer = 'fixed');
"""

WEB_REPORT_REQUESTS = (
    r"""
    -- A DM's "make the report again" for one session, asked for on the website (#2661), waiting
    -- for the bot. The website's role cannot touch the memory tables or call the extractor, so its
    -- door only checks the signed-in DM and queues the request here; the bot reads the row, runs
    -- the same step that makes the report after a session (`report_make.make`, with its plan check
    -- and call limit), and closes the row. How far the making has got is the report's own run
    -- (`memory_report_runs`), which the page already reads: this row only says "asked for, not
    -- taken yet" and, if the bot gave up, "failed". Counts and times only, no word of any story.
    -- One open request per session; a row lives a day at most, is gone with its campaign, left
    -- out of backups and isolated per server.
    CREATE TABLE web_report_requests (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        session_at  BIGINT NOT NULL CHECK (session_at > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_report_requests_open ON web_report_requests (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_report_requests_one_open
        ON web_report_requests (guild_id, campaign_id, session_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_report_requests")
    + r"""
    -- Ask for the report of one session again. The same guard as the report's read: a DM of the
    -- campaign, in a server on the session's list (NULL for anyone else). A request already open
    -- for the session comes back as it is (a second press is safe); a report being made right now
    -- (its run touched in the last fifteen minutes, the store's own limit) is 'busy'; five
    -- requests an hour for a campaign is 'too_soon'. A request the bot took and went quiet on for
    -- ten minutes goes back to waiting. 'ok' with the id otherwise. The page checks that the
    -- session is the campaign's own (the door checks it too).
    CREATE FUNCTION dmbot_web_report_make(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('report_request:' || g::TEXT || ':' || c, 0));
        DELETE FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- (Ten minutes quiet puts a taken request back; the page calls a run stalled from fifteen,
        -- the store's own limit, so the DM hears of it before it is put back.)
        UPDATE web_report_requests r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        -- A request nobody took in half an hour (the bot is off or gone) ends as failed, so the
        -- page says so and the next press is not stuck behind it for a day.
        UPDATE web_report_requests r SET state = 'failed', error = 'not_taken', updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'waiting'
              AND r.created_at < now_s - 1800;
        IF NOT EXISTS (SELECT 1 FROM transcript_sessions t
                       WHERE t.guild_id = g AND t.campaign_id = c AND t.started_at = at_) THEN
            RETURN jsonb_build_object('status', 'no_session');
        END IF;
        SELECT r.id INTO have FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_
              AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF EXISTS (SELECT 1 FROM memory_report_runs m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.session_at = at_
                     AND m.state = 'making' AND m.updated_at >= now_s - 900) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_report_requests (guild_id, campaign_id, id, user_id, session_at,
                                         created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), at_, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The newest request for one session (within a day), for a DM of the campaign: where it
    -- stands and when, never who asked. NULL when there is none (or for anyone else).
    CREATE FUNCTION dmbot_web_report_request(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_report_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_report_requests q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.session_at = at_
              AND q.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400
            ORDER BY q.seq DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_make(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_request(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report_make(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_report_request(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""
)

GAME_MASTERS_READ = r"""
    -- A campaign's game masters for the website's Settings tab (#2768), and the people who can be
    -- offered as a new one. Read only; both are for a DM of the campaign alone (NULL for anyone
    -- else), in a server on the session's list. A name is the campaign's own person for that
    -- account if there is one, else the name the bot last saw in the server, else the website's.
    -- The website's name table is opened for the one call (as the import page's member search
    -- does) and shut again. The candidates are the campaign's own people and the server's members
    -- who agreed to be recorded under the current terms (`terms`, as the import page's search
    -- passes it; an older agreement is not one), never a game master already, at most 25: no
    -- free-typed account ever reaches the queue, and nobody who has not agreed is listed.
    CREATE FUNCTION dmbot_web_game_masters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        listed JSONB;
        owner_id BIGINT;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT k.owner_user_id INTO owner_id
        FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'userId', d.user_id::TEXT,
                   'name', COALESCE(
                       (SELECT e.name FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                          AND e.status = 'confirmed' AND e.played_by = d.user_id
                        ORDER BY e.name LIMIT 1),
                       (SELECT CASE WHEN m.display_name <> '' THEN m.display_name
                                    ELSE m.username END
                        FROM member_directory m
                        WHERE m.guild_id = g AND m.user_id = d.user_id
                          AND (m.display_name <> '' OR m.username <> '')),
                       (SELECT u.display_name FROM web_users u
                        WHERE u.user_id = d.user_id AND COALESCE(u.display_name, '') <> ''),
                       ''),
                   'plan', d.user_id = owner_id,
                   'you', d.user_id = dmbot_current_user())
               ORDER BY (d.user_id = owner_id) DESC, d.user_id), '[]'::jsonb)
        INTO listed
        FROM campaign_dms d WHERE d.guild_id = g AND d.campaign_id = c;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN listed;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_game_masters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_game_masters(BIGINT, TEXT) FROM PUBLIC;

    CREATE FUNCTION dmbot_web_game_master_candidates(g BIGINT, c TEXT, q TEXT, terms INT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        listed JSONB;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE(jsonb_agg(jsonb_build_object('userId', m.user_id::TEXT, 'name', m.name)
                        ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name)),
                        '[]'::jsonb)
        INTO listed
        FROM (SELECT one.user_id, one.name, one.rank FROM (
                  -- One row for each account: the campaign's name for the person if there is one,
                  -- else the server directory's. A search matches either name.
                  SELECT n.user_id,
                         (array_agg(n.name ORDER BY n.rank, lower(n.name)))[1] AS name,
                         min(n.rank) AS rank,
                         lower(string_agg(n.hay, ' ')) AS hay
                  FROM (
                      SELECT e.played_by AS user_id, e.name, 0 AS rank, e.name AS hay
                      FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                        AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                      UNION ALL
                      SELECT m.user_id,
                             CASE WHEN m.display_name <> '' THEN m.display_name ELSE m.username END,
                             1, m.display_name || ' ' || m.username
                      FROM member_directory m
                      WHERE m.guild_id = g AND (m.display_name <> '' OR m.username <> '')
                  ) n
                  WHERE EXISTS (SELECT 1 FROM consent x
                                WHERE x.guild_id = g AND x.user_id = n.user_id
                                  AND x.terms_version = terms)
                    AND NOT EXISTS (SELECT 1 FROM campaign_dms d
                                    WHERE d.guild_id = g AND d.campaign_id = c
                                      AND d.user_id = n.user_id)
                  GROUP BY n.user_id
              ) one
              WHERE q = '' OR position(lower(q) IN one.hay) > 0
              ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)
              LIMIT 25) m;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN listed;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
"""

GAME_MASTER_SETTING = r"""
    -- A fifth choice on the website's Settings queue (#2768): a game master added or removed.
    -- `setting` also accepts 'co_dm'; `value` is the person's id (digits, at most 19) and `turn_on`
    -- says add (true) or remove (false), so `turn_on` is now set exactly for the two settings that
    -- carry a yes or no of their own (an optional rule, a game master) and stays NULL for the rest.
    -- No row changes: no backfill policy is needed.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_turn_on_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm')),
        ADD CONSTRAINT setting_actions_turn_on_check
            CHECK ((setting IN ('optional_rule', 'co_dm')) = (turn_on IS NOT NULL));

    -- Queue one game master change. Same guard as `dmbot_web_setting_add`: the caller is a DM of
    -- the campaign in a server on the session's list, and the campaign's queue is not full. Quick
    -- refusals only, so the page answers at once; the bot checks every rule again when it applies
    -- the change (who may, the limit, the plan holder, the last game master, a bot, a person who
    -- has left). An add is for someone who agreed to be recorded in this server, the people the
    -- candidates door lists: a made-up id is 'unknown_person', so no free-typed account is queued.
    -- 'ok' with the id, 'bad_value' for a malformed id or no yes/no, 'plan_holder' for removing
    -- the one whose plan runs the campaign, 'too_many' for an add at the limit (`max_dms`, the
    -- store's MAX_DMS, passed in so the number lives in one place), 'unknown_person', 'too_soon';
    -- NULL for anyone not a DM.
    CREATE FUNCTION dmbot_web_co_dm_add(
        g BIGINT, c TEXT, uid TEXT, on_ BOOLEAN, terms INT, max_dms INT
    )
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        who BIGINT;
        owner_id BIGINT;
        have INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- The lock first, so two quick adds cannot both pass the limit check below.
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        IF uid IS NULL OR uid !~ '^[1-9][0-9]{0,18}$' OR on_ IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        who := uid::BIGINT;
        SELECT k.owner_user_id INTO owner_id
            FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT on_ AND who = owner_id THEN
            RETURN jsonb_build_object('status', 'plan_holder');
        END IF;
        IF on_ THEN
            SELECT count(*) INTO have FROM campaign_dms d
                WHERE d.guild_id = g AND d.campaign_id = c;
            IF have >= max_dms AND NOT EXISTS (SELECT 1 FROM campaign_dms d
                                         WHERE d.guild_id = g AND d.campaign_id = c
                                           AND d.user_id = who) THEN
                RETURN jsonb_build_object('status', 'too_many');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM consent x WHERE x.guild_id = g AND x.user_id = who
                           AND x.terms_version = terms) THEN
                RETURN jsonb_build_object('status', 'unknown_person');
            END IF;
        END IF;
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, turn_on,
                                     created_at)
            VALUES (g, c, qid, dmbot_current_user(), 'co_dm', uid, on_, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', qid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT) FROM PUBLIC;
"""


# "Not named yet" on the website (#2803): a DM's Names page lists the people the story described and
# never named (0263), and gives them a name through the queue the other name actions use. Three
# new actions: `name` (a new name for them: the bot renames, or answers `known_name` with the one
# DMbot already knows, or `ambiguous` / `gone`), `name-join` (the DM said "same person": the bot
# joins them into the named one, keeping it) and `name-undo` (the done one of those two, undone
# with the batch the bot kept in `result_other`). The two doors and the action list are read back
# from the database as they are now and widened, one place at a time, each of which must be found
# exactly once: a door whose text changed under this migration stops it instead of passing
# silently. A confirmed unnamed person never takes the other actions (a spelling, a kind, a secret):
# those are for named entries.
def _swaps(var: str, pairs: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(
        f"        {var} := pg_temp.swap_once({var}, $o${old}$o$, $n${new}$n$);"
        for old, new in pairs
    )


_NAME_DOOR_SWAPS = (
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n        THEN",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                'name', 'name-join', 'name-undo')\n        THEN",
    ),
    (
        "(act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words')\n"
        "               AND clean = '')",
        "(act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words',\n"
        "                   'name')\n"
        "               AND clean = '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND clean <> '')",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n               AND clean <> '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND (tgt IS NULL OR",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n               AND (tgt IS NULL OR",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name-join', 'name-undo')\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo')\n"
        "               AND COALESCE(fl, false))",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name', 'name-join', 'name-undo')\n"
        "               AND COALESCE(fl, false))",
    ),
    (
        "OR act LIKE 'names-fixed-%' THEN",
        "OR act LIKE 'names-fixed-%' OR act = 'name-undo' THEN",
    ),
    (
        "ELSIF act = 'reading-undo' AND NOT EXISTS (",
        "ELSIF act = 'name-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.id = tgt AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action IN ('name', 'name-join') AND p.state = 'done'\n"
        "                   AND p.result_other <> '') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
    ),
    (
        "                 AND e.status = 'confirmed' AND e.type <> 'person') THEN",
        "                 AND ((e.status = 'confirmed' AND e.type <> 'person' AND NOT e.unnamed\n"
        "                       AND act NOT IN ('name', 'name-join'))\n"
        "                      OR (act IN ('name', 'name-join') AND e.unnamed\n"
        "                          AND e.status IN ('proposed', 'confirmed')))) THEN",
    ),
    (
        "IF act IN ('merge', 'link-add') AND (tgt = subject OR NOT EXISTS (",
        "IF act IN ('merge', 'link-add', 'name-join') AND (tgt = subject OR NOT EXISTS (",
    ),
    (
        "                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN",
        "                 AND t.status = 'confirmed' AND t.type <> 'person'\n"
        "                 AND NOT t.unnamed)) THEN",
    ),
    (
        "                     AND t.status = 'confirmed' AND t.type <> 'person')\n"
        "                  <> cardinality(",
        "                     AND t.status = 'confirmed' AND t.type <> 'person'\n"
        "                     AND NOT t.unnamed)\n"
        "                  <> cardinality(",
    ),
)

_NAMES_PAGE_SWAPS = (
    (
        "SELECT e.id, e.name, e.created_at,\n",
        "SELECT e.id, e.name, e.created_at, e.unnamed, e.unnamed_what, e.unnamed_anchor,\n",
    ),
    (
        "CASE WHEN e.type = 'character' THEN",
        "CASE WHEN e.unnamed THEN 'not_named' WHEN e.type = 'character' THEN",
    ),
    (
        "              AND e.status = 'confirmed' AND e.type <> 'person'\n",
        "              AND ((e.status = 'confirmed' AND e.type <> 'person')\n"
        "                   OR (is_dm AND e.unnamed AND e.status IN ('proposed', 'confirmed')\n"
        "                       AND COALESCE(current_setting('dmbot.names_unnamed', true), '')\n"
        "                           = '1'))\n",
    ),
    (
        "'hidden', p.hidden,",
        "'hidden', p.hidden, 'unnamed', p.unnamed, 'unnamedWhat', p.unnamed_what,\n"
        "                    'unnamedAnchor', (\n"
        "                        SELECT jsonb_build_object(\n"
        "                            'name', x.name, 'what', x.unnamed_what,\n"
        "                            'anchorName', y.name)\n"
        "                        FROM memory_entities x\n"
        "                        LEFT JOIN memory_entities y ON y.guild_id = x.guild_id\n"
        "                             AND y.campaign_id = x.campaign_id\n"
        "                             AND y.id = x.unnamed_anchor\n"
        "                        WHERE x.guild_id = g AND x.campaign_id = c\n"
        "                          AND x.id = p.unnamed_anchor),",
    ),
)

WEB_NAME_UNNAMED = (
    r"""
    CREATE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
        con text;
    BEGIN
        SELECT pg_get_constraintdef(c.oid) INTO con FROM pg_constraint c
            WHERE c.conrelid = 'name_actions'::regclass AND c.conname = 'name_actions_action_check';
        IF con IS NULL OR position($q$'spell'::text,$q$ IN con) = 0 THEN
            RAISE EXCEPTION 'the name action list changed under this migration';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(
                con, $q$'spell'::text,$q$,
                $q$'spell'::text, 'name'::text, 'name-join'::text, 'name-undo'::text,$q$);

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';
"""
    + _swaps("def", _NAME_DOOR_SWAPS)
    + r"""
        EXECUTE def;

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_all_names';
"""
    + _swaps("def", _NAMES_PAGE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

WEB_RESTORES = (
    """
    -- A backup loaded from the website (#2745), waiting for the bot. The website's role cannot
    -- write the campaign tables, so it stages the file here through the doors below, and the bot
    -- applies it with the same code as Discord's `/dmbot restore`: after checking again the plan,
    -- the free campaign slot, that the person is still in the server and (to replace a campaign)
    -- still one of its DMs, and that the campaign is not playing. `main` is the copy and
    -- `web_restore_parts` its transcripts files (all of them or none, as in Discord). One row is
    -- one restore: `receiving` while the person's own files are still arriving, then `waiting`,
    -- `working` (with `stage`, `done_parts` of `parts_wanted` and `updated_at` for the shared
    -- progress shape of #2564), then `done` or `failed` with a plain reason word. The bytes are
    -- emptied when the bot has applied or refused it, and the row goes 24 hours after it began,
    -- whichever is first (the bot sweeps; the doors also do). A copy holds the campaign's secrets,
    -- like any backup, so it is never logged. Isolated per server, read by the person who made it
    -- only, one pending restore per person per server, and not itself in a backup.
    CREATE TABLE web_restores (
        guild_id      BIGINT NOT NULL,
        id            TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id       BIGINT NOT NULL CHECK (user_id > 0),
        -- The campaign to replace, or NULL for a new one. Not a foreign key: the bot judges whether
        -- it still exists and is the person's, and a row must outlive a deleted campaign to say so.
        replace_id    TEXT,
        name          TEXT NOT NULL CHECK (char_length(name) <= 100),
        parts_wanted  INT NOT NULL CHECK (parts_wanted BETWEEN 0 AND 24),
        main          BYTEA,
        state         TEXT NOT NULL DEFAULT 'receiving'
            CHECK (state IN ('receiving', 'waiting', 'working', 'done', 'failed')),
        stage         TEXT NOT NULL DEFAULT 'restore_receiving'
            CHECK (stage IN ('restore_receiving', 'restore_waiting', 'restore_reading',
                             'restore_writing', 'done', 'failed')),
        error         TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        done_parts    INT NOT NULL DEFAULT 0,
        left_out      INT NOT NULL DEFAULT 0,
        without_parts BOOLEAN NOT NULL DEFAULT FALSE,
        result_id     TEXT,
        created_at    BIGINT NOT NULL,
        claimed_at    BIGINT NOT NULL DEFAULT 0,
        updated_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_restores_open ON web_restores (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    -- One restore on its way per person across every server (a copy can be 625 MB): the index sees
    -- all servers' rows even though a door sees one server's.
    CREATE UNIQUE INDEX web_restores_one_each ON web_restores (user_id)
        WHERE state IN ('receiving', 'waiting', 'working');
    CREATE TABLE web_restore_parts (
        guild_id   BIGINT NOT NULL,
        restore_id TEXT NOT NULL,
        n          INT NOT NULL CHECK (n BETWEEN 1 AND 24),
        data       BYTEA,
        -- ON UPDATE CASCADE: a foreign key that carries guild_id (CLAUDE.md, #2550).
        FOREIGN KEY (guild_id, restore_id)
            REFERENCES web_restores (guild_id, id) ON DELETE CASCADE ON UPDATE CASCADE,
        PRIMARY KEY (guild_id, restore_id, n)
    );
    """
    + _isolate("web_restores")
    + _isolate("web_restore_parts")
    + r"""
    -- The bytes never outlive the end of a restore, whatever the bot does: the moment a row turns
    -- done or failed, the copy is emptied and its transcripts files go (the 24-hour sweep catches
    -- the rows that never ended).
    CREATE FUNCTION web_restores_empty() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        IF NEW.state IN ('done', 'failed') THEN
            NEW.main := NULL;
            DELETE FROM web_restore_parts p
                WHERE p.guild_id = NEW.guild_id AND p.restore_id = NEW.id;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER web_restores_empty BEFORE UPDATE ON web_restores
        FOR EACH ROW EXECUTE FUNCTION web_restores_empty();

    -- Stage a restore: the copy itself, already checked by the API (a backup of this name, asking
    -- for this many transcripts files). `replace_` is a campaign of the caller's to swap for the
    -- copy, or NULL for a new campaign. The door checks what it can see: the caller is in the
    -- server (and, to replace, a DM of that campaign), no other restore of theirs is still pending
    -- in any server, and fewer than 5 in the last hour here. Answers 'ok' with the id and the state
    -- (`receiving`), 'busy' with the id of the one pending, 'too_soon', or 'bad_target'; NULL for
    -- anyone not in the server.
    CREATE FUNCTION dmbot_web_restore_add(
        g BIGINT, replace_ TEXT, nm TEXT, wanted INT, copy BYTEA
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL OR g IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF copy IS NULL OR nm IS NULL OR wanted IS NULL OR wanted < 0 OR wanted > 24 THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        IF replace_ IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM campaign_dms d
               WHERE d.guild_id = g AND d.campaign_id = replace_ AND d.user_id = me) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('web_restore:' || me::TEXT, 0));
        DELETE FROM web_restores r WHERE r.guild_id = g AND r.created_at < now_s - 86400;
        SELECT r.id INTO have FROM web_restores r
            WHERE r.guild_id = g AND r.user_id = me
              AND r.state IN ('receiving', 'waiting', 'working')
            ORDER BY r.created_at DESC LIMIT 1;
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        -- (Counted in this server only; the API's per-person rate limit is the real one.)
        IF (SELECT count(*) FROM web_restores r
            WHERE r.guild_id = g AND r.user_id = me AND r.created_at > now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        -- Always `receiving` first: nothing goes to the bot until the person has seen what it
        -- will do and pressed Load it (`dmbot_web_restore_go`), even with no transcripts files.
        BEGIN
            INSERT INTO web_restores (guild_id, id, user_id, replace_id, name, parts_wanted, main,
                                      created_at, updated_at)
                VALUES (g, rid, me, replace_, left(nm, 100), wanted, copy, now_s, now_s);
        EXCEPTION WHEN unique_violation THEN
            -- Pending in another server (this server's was found above): no id to give.
            RETURN jsonb_build_object('status', 'busy');
        END;
        RETURN jsonb_build_object('status', 'ok', 'id', rid, 'state', 'receiving');
    END
    $fn$;

    -- One transcripts file of a restore that is still receiving, numbered 1 to the count the copy
    -- asked for; sending a number again replaces it. 'ok' with how many have arrived, 'bad_part'
    -- for a number outside the count, 'not_receiving' once it is on its way; NULL when it is not
    -- the caller's.
    CREATE FUNCTION dmbot_web_restore_part(g BIGINT, rid TEXT, num INT, blob BYTEA) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_restores%ROWTYPE;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state <> 'receiving' THEN
            RETURN jsonb_build_object('status', 'not_receiving');
        END IF;
        IF num IS NULL OR num < 1 OR num > r.parts_wanted OR blob IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_part');
        END IF;
        INSERT INTO web_restore_parts (guild_id, restore_id, n, data) VALUES (g, rid, num, blob)
            ON CONFLICT (guild_id, restore_id, n) DO UPDATE SET data = EXCLUDED.data;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        RETURN jsonb_build_object('status', 'ok', 'received', got);
    END
    $fn$;

    -- Send it to the bot: with every transcripts file in (`without` false), or restoring without
    -- them (`without` true, as Discord allows: all of them or none). 'ok', 'missing' when files
    -- are still to come, 'not_receiving' if it already went; NULL when it is not the caller's.
    CREATE FUNCTION dmbot_web_restore_go(g BIGINT, rid TEXT, without BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_restores%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state <> 'receiving' THEN
            RETURN jsonb_build_object('status', 'not_receiving');
        END IF;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        IF COALESCE(without, false) THEN
            DELETE FROM web_restore_parts p WHERE p.guild_id = g AND p.restore_id = rid;
        ELSIF got <> r.parts_wanted THEN
            RETURN jsonb_build_object('status', 'missing', 'received', got);
        END IF;
        UPDATE web_restores x SET state = 'waiting', stage = 'restore_waiting',
               without_parts = COALESCE(without, false), updated_at = now_s
            WHERE x.guild_id = g AND x.id = rid;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Where a restore stands, for the person who made it (nobody else): the progress fields, the
    -- name of the copy (the campaign's own name, which they supplied), how many transcripts files
    -- it asks for and has, and, once it has ended, the plain reason word and how many lines were
    -- left out for people who had deleted theirs. NULL when it is not theirs.
    CREATE FUNCTION dmbot_web_restore_read(g BIGINT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
        got INT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- Named columns, so a poll never reads the copy itself.
        SELECT x.state, x.stage, x.error, x.name, x.parts_wanted, x.done_parts, x.left_out,
               x.replace_id, x.result_id, x.without_parts, x.created_at, x.updated_at INTO r
            FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
              AND x.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT count(*) INTO got FROM web_restore_parts p
            WHERE p.guild_id = g AND p.restore_id = rid;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'name', r.name, 'partsWanted', r.parts_wanted, 'partsReceived', got,
            'doneParts', r.done_parts, 'leftOut', r.left_out, 'replaceId', r.replace_id,
            'resultId', r.result_id, 'withoutParts', r.without_parts,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;

    -- The caller's own pending restore in this server, if any (id only), so a page that was
    -- closed can find its way back.
    CREATE FUNCTION dmbot_web_restore_pending(g BIGINT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out TEXT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT x.id INTO out FROM web_restores x
            WHERE x.guild_id = g AND x.user_id = dmbot_current_user()
              AND x.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400
            ORDER BY x.created_at DESC LIMIT 1;
        RETURN out;
    END
    $fn$;

    -- Take it back: the person's own restore, while the bot has not started it. The bytes go with
    -- the row. True when something was removed.
    CREATE FUNCTION dmbot_web_restore_cancel(g BIGINT, rid TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        st TEXT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT x.state INTO st FROM web_restores x
            WHERE x.guild_id = g AND x.id = rid AND x.user_id = dmbot_current_user()
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'gone';
        END IF;
        -- The bot has it: it cannot be taken back, and the page must go on showing it.
        IF st = 'working' THEN
            RETURN 'working';
        END IF;
        DELETE FROM web_restores x WHERE x.guild_id = g AND x.id = rid;
        RETURN 'ok';
    END
    $fn$;
    -- Who owns the campaign a restore would replace, for the plan check made before any bytes are
    -- stored: the owner's id as text (or '' when it has none), only for a DM of that campaign in
    -- this server. NULL for anyone else.
    CREATE FUNCTION dmbot_web_restore_target(g BIGINT, c TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        own BIGINT;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT k.owner_user_id INTO own FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        RETURN COALESCE(own::TEXT, '');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_target(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_pending(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_restore_cancel(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_restore_target(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_add(BIGINT, TEXT, TEXT, INT, BYTEA) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_part(BIGINT, TEXT, INT, BYTEA) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_go(BIGINT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_pending(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_restore_cancel(BIGINT, TEXT) FROM PUBLIC;
"""
)


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
    ("0075_support_email", SUPPORT_EMAIL),  # the admin page's door to a ticket's reply address
    (
        "0076_claims_by_fact",
        MEMORY_CLAIMS_BY_FACT,
    ),  # the recall service finds a fact's claim (#1350)
    ("0077_extractor_progress", EXTRACTOR_PROGRESS),  # a restart resumes the extractor (#1367)
    ("0078_support_own", SUPPORT_OWN),  # a person's own questions (0077: another PR)
    ("0079_recap_jobs", RECAP_JOBS),  # the cinematic recap job of a session (#1382)
    ("0080_recap_audio_addon", RECAP_AUDIO_ADDON),  # the recap audio add-on (#1389)
    ("0081_recap_audio_follow_ups", RECAP_AUDIO_FOLLOW_UPS),  # move + stray sweep (#1399)
    ("0082_recap_audio_jobs", RECAP_AUDIO_JOBS),  # the recap's audio tracks (#1383)
    ("0083_memory_said_as", MEMORY_SAID_AS),  # a spelling for the narrator (#1419)
    ("0084_recap_switches", RECAP_SWITCHES),  # the DM's recap choices (#1421)
    ("0085_recap_audio_again", RECAP_AUDIO_AGAIN),  # the website's door to new audio (#1418)
    ("0086_support_thread", SUPPORT_THREAD),  # the support flow's replies (#1431)
    ("0087_web_names", WEB_NAMES),  # the website's doors to the names and their spelling (#1451)
    ("0088_support_mine_answer", SUPPORT_MINE_ANSWER),  # a person's own latest answer (#1432)
    ("0089_memory_from_set", MEMORY_FROM_SET),  # sets carry relationship definitions (#1456)
    ("0090_support_reason", SUPPORT_REASON),  # why a ticket went to a person (#1459)
    ("0091_web_recap_choices", WEB_RECAP_CHOICES),  # the DM's recap choices on the site (#1468)
    ("0092_support_handed_over", SUPPORT_HANDED_OVER),  # every hand-over, kept (#1487)
    ("0093_transcript_suggestions", TRANSCRIPT_SUGGESTIONS),  # a player's suggested fix (#1491)
    ("0094_web_transcripts", WEB_TRANSCRIPTS),  # the website reads a campaign's transcripts (#1501)
    ("0095_web_transcript_pages", WEB_TRANSCRIPT_PAGES),  # transcript pages and characters (#1512)
    (
        "0096_support_week_mail",
        SUPPORT_WEEK_MAIL,
    ),  # the weekly support email's once-only mark (#1514)
    ("0097_homebrew_entries", HOMEBREW_ENTRIES),  # a campaign's own homebrew (#1520)
    ("0098_web_house_rules", WEB_HOUSE_RULES),  # the website reads a campaign's house rules (#1521)
    ("0099_homebrew_from_book", HOMEBREW_FROM_BOOK),  # homebrew from a confirmed book (#1524)
    ("0100_web_homebrew", WEB_HOMEBREW),  # the website reads a campaign's shown homebrew (#1525)
    ("0101_transcript_keep", TRANSCRIPT_KEEP),  # how long a campaign keeps transcripts (#1533)
    ("0102_pack_suggestions", PACK_SUGGESTIONS),  # suggest an entry to a pack (#1537)
    ("0103_web_transcript_deleted", WEB_TRANSCRIPT_DELETED),  # the site shows deleted ones (#1548)
    ("0104_homebrew_book_edition", HOMEBREW_BOOK_EDITION),  # which rules a book is for (#1563)
    (
        "0105_web_transcript_keep",
        WEB_TRANSCRIPT_KEEP,
    ),  # the site says how long transcripts are kept (#1575)
    ("0106_waiting_name_questions", WAITING_NAME_QUESTIONS),  # unanswered "Did they mean…?" (#1572)
    ("0107_web_homebrew_book", WEB_HOMEBREW_BOOK),  # the page tags shared-book entries (#1579)
    ("0108_web_homebrew_packs", WEB_HOMEBREW_PACKS),  # the page shows followed packs (#1586)
    ("0109_game_clock_groups", GAME_CLOCK_GROUPS),  # split-party clocks (#1607)
    ("0110_improvement_opt_outs", IMPROVEMENT_OPT_OUTS),  # the improvement opt-out (#1609)
    ("0111_homebrew_rules_to_house_rules", HOMEBREW_RULES_TO_HOUSE_RULES),  # #1628
    ("0112_homebrew_enabled", HOMEBREW_ENABLED),  # each entry on or off for the table (#1629)
    ("0113_homebrew_sources", HOMEBREW_SOURCES),  # an entry's source, one page per address (#1633)
    ("0114_homebrew_pack_doors", HOMEBREW_PACK_DOORS),  # the site's doors read pointers (#1635)
    ("0115_clue_audience", CLUE_AUDIENCE),  # a clue only some people know (#1649)
    ("0116_web_homebrew_source", HOMEBREW_SOURCE_DOOR),  # the page says where it came from (#1653)
    ("0117_standing", STANDING),  # impressions, attitudes and the party (#1655)
    ("0118_story_threads", STORY_THREADS),  # a thread's states beside the conditions (#1662)
    ("0119_homebrew_more_kinds", MORE_HOMEBREW_KINDS),  # species, subclass, background (#1663)
    ("0120_dm_bot_channel", DM_BOT_CHANNEL),  # the DM Bot channel and its panel (#1666)
    ("0121_story_mode", STORY_MODE),  # the Shared story switch (#1692)
    ("0122_adventure_text", ADVENTURE_TEXT),  # the DM's adventure text, in chunks (#1698)
    ("0123_story_plan", STORY_PLAN),  # the story plan read from the adventure (#1714)
    (
        "0124_plan_same_as",
        PLAN_SAME_AS,
    ),  # the book's entry is the table's, by the DM's word (#1715)
    ("0125_plan_divergences", PLAN_DIVERGENCES),  # the table has left the adventure (#1729)
    ("0126_plan_position", PLAN_POSITION),  # the chapter the DM says the table is in (#1731)
    ("0127_rules_channel", RULES_CHANNEL),  # the rules archive channel (#1752)
    ("0128_npcs_channel", NPCS_CHANNEL),  # the NPC roster channel (#1761)
    ("0129_initiative_style", INITIATIVE_STYLE),  # the initiative style FightBot heard (#1784)
    ("0130_initiative_style_backfill", INITIATIVE_STYLE_BACKFILL),  # old switch folded in (#1791)
    (
        "0131_recap_to_confirm",
        RECAP_TO_CONFIRM,
    ),  # names a recap kept, for the DM to confirm (#2027)
    ("0132_quiet_stop", QUIET_STOP),  # minutes of silence before an automatic stop (#2034)
    ("0133_recap_style", RECAP_STYLE),  # examples a DM gives for how recaps sound (#2075)
    ("0134_recap_style_notes", RECAP_STYLE_NOTES),  # style notes and corrections (#2076)
    ("0135_recap_approval", RECAP_APPROVAL),  # approve the text, then pick the audio (#2083)
    ("0136_session_imports", SESSION_IMPORTS),  # sessions loaded from a file (#2105)
    ("0137_recap_share", RECAP_SHARE),  # a public link for a posted recap (#2118)
    ("0138_member_directory", MEMBER_DIRECTORY),  # who an imported name can be (#2141)
    ("0139_recap_requests", RECAP_REQUESTS),  # the recap page's notes to DMbot (#2113)
    ("0140_recap_voice", RECAP_VOICE),  # ... and a short clip it may speak instead (#2113)
    ("0141_recap_holds", RECAP_HOLDS),  # a recap waits for the DM's name answers (#2169)
    ("0142_plan_pending", PLAN_PENDING),  # a plan change set for the period's end (#2165)
    ("0143_old_spelling", OLD_SPELLING),  # a fixed spelling keeps the old one (#2170)
    ("0144_transcript_respells", TRANSCRIPT_RESPELLS),  # where a fixed name was respelled (#2170)
    ("0145_recap_respelled", RECAP_RESPELLED),  # a recap's text followed a fixed spelling (#2170)
    ("0146_own_writing", OWN_WRITING),  # a DM's own background is its own purpose (#2167)
    ("0147_said_as_suggestions", SAID_AS_SUGGESTIONS),  # say it this way? (#2171)
    ("0148_support_mail_ids", SUPPORT_MAIL_IDS),  # email threading (#2192)
    (
        "0149_audio_again_posted",
        RECAP_AUDIO_AGAIN_POSTED,
    ),  # make it again for a posted recap (#2183)
    (
        "0150_support_mine_thread",
        SUPPORT_MINE_THREAD,
    ),  # a person reads their own ticket's thread (#2193)
    ("0151_recap_hold_names", RECAP_HOLD_NAMES),  # how many answers it waits on (#2174)
    ("0152_recap_hold_reminded", RECAP_HOLD_REMINDED),  # the one reminder it gets (#2174)
    ("0153_import_recaps", IMPORT_RECAPS),  # Write recaps for an import, its scan window (#2106)
    ("0154_import_progress", IMPORT_PROGRESS),  # each import session's stage, scan given up
    (
        "0155_support_ticket_times",
        SUPPORT_TICKET_TIMES,
    ),  # no two of a person's tickets share a time
    ("0156_names_later", NAMES_LATER),  # a "Later" that stops holding a recap (#2174)
    ("0157_name_answers", NAME_ANSWERS),  # answering a recap's names to check on its page (#2113)
    ("0158_recap_split", RECAP_SPLIT),  # a session's opening split into parts (#2253)
    ("0159_npc_notes", NPC_NOTES),  # an NPC's page: its listed view and private notes (#2265)
    ("0160_installs_left_except", INSTALLS_LEFT_EXCEPT),  # servers left while offline (#2166)
    ("0161_import_suggest", IMPORT_SUGGEST),  # who a name is, and who they play (#2135)
    ("0162_recap_critics", RECAP_CRITICS),  # the critics loop's saved state (#2255)
    ("0163_import_plays", IMPORT_PLAYS),  # the DM's "plays" choices wait for the bot (#2135)
    ("0164_house_rule_wins", HOUSE_RULE_WINS),  # which of two disagreeing rules wins (#2284)
    ("0165_house_rule_source", HOUSE_RULE_SOURCE),  # where each house rule came from (#2285)
    ("0166_quiet_stop_choices", QUIET_STOP_CHOICES_SQL),  # no Off, 5/10/15/30, default 10 (#2288)
    ("0167_hidden_from_players", HIDDEN_FROM_PLAYERS),  # a name the players don't know yet (#2321)
    ("0168_email_changes", EMAIL_CHANGES),  # a waiting change of the account email (#2349)
    ("0169_made_before_name_gate", NAME_GATE),  # suggestions from before the pronoun gate (#2358)
    ("0170_all_names_page", ALL_NAMES_PAGE),  # the website's All names list (#2294)
    ("0171_all_names_actions", NAME_ACTIONS),  # the DM's actions on that page, for the bot (#2294)
    ("0172_house_file_read", HOUSE_FILE_READ),  # the AI's last read of a linked file (#2377)
    ("0173_backstory_links", BACKSTORY_LINKS),  # connections a backstory found, waiting (#2380)
    ("0174_all_names_speed", ALL_NAMES_SPEED),  # its secret-name check in one join (#2376)
    ("0175_recap_address_co_dm", RECAP_ADDRESS_CO_DM),  # a co-DM's recap email address (#2360)
    ("0176_transcript_speaker_names", TRANSCRIPT_SPEAKER_NAMES),  # names on the website (#2337)
    ("0177_web_relationships", WEB_RELATIONSHIPS),  # the relationship web's data (#2323)
    ("0178_web_link_actions", WEB_LINK_ACTIONS),  # edits to the web's connections (#2323)
    ("0179_beliefs", BELIEFS),  # perspectives, beliefs and the backfill (#2409)
    ("0180_shared_recap_session", SHARED_RECAP_SESSION),  # a shared recap's session (#2418)
    ("0181_campaign_card", CAMPAIGN_CARD),  # a campaign's name for its page (#2416)
    ("0182_share_unposted", SHARE_UNPOSTED),  # a link for an approved, unposted recap (#2419)
    ("0183_merge_many", MERGE_MANY + ALL_NAMES_MENTIONS),  # merge several names (#2426)
    ("0184_recap_posting_clock", RECAP_POSTING_CLOCK),  # the posting clock (#2435)
    ("0185_web_graph_party", WEB_GRAPH_PARTY),  # the web reads The party's beliefs (#2409)
    ("0186_web_graph_views", WEB_GRAPH_VIEWS),  # Players / Full View and the peek notice (#2404)
    ("0187_unrevealed_connections", UNREVEALED_CONNECTIONS),  # held-back connections (#2404)
    ("0188_all_names_said", ALL_NAMES_SAID),  # how to say a name, for the Names page (#2317)
    ("0189_recap_held_names", RECAP_HELD_NAMES),  # the website's recap check, as the bot's (#2404)
    ("0190_pinned_truth", PINNED_TRUTH),  # the DM's 📌 100% truth (#2447)
    ("0191_backstory_link_words", BACKSTORY_LINK_WORDS),  # catch-all words on waiting links (#2454)
    ("0192_campaign_facts", CAMPAIGN_FACTS),  # each campaign card's facts (#2460)
    ("0193_link_pin_action", LINK_PIN_ACTION),  # the website pins a connection (#2447)
    ("0194_shared_rules_page", SHARED_RULES_PAGE),  # the Shared rules page (#2460)
    ("0195_session_pauses", SESSION_PAUSES),  # a restart keeps the pauses (#2466)
    ("0196_story_timeline", STORY_TIMELINE),  # recaps and reveals for the timeline (#2462)
    ("0197_web_setting_actions", SETTING_ACTIONS),  # settings chosen on the website (#2463)
    ("0198_off_record_lines", OFF_RECORD_LINES),  # the "off the record" marker line (#2466)
    ("0199_merge_restores", MERGE_RESTORES),  # where a merge moved notes and a sheet (#2438)
    ("0200_secret_exposures", SECRET_EXPOSURES),  # secrets said at the table, recorded (#2410)
    ("0201_exposure_summary", EXPOSURE_SUMMARY),  # the summary applies exposures (#2410)
    ("0202_image_lines", IMAGE_LINES),  # a line that describes an image in the table chat (#2450)
    ("0203_map_notes", MAP_NOTES),  # a map put into words, kept for MapBot (#2450)
    ("0204_relation_directional", RELATION_DIRECTIONAL),  # family roles read one way (#2457)
    (
        "0205_recap_call_log",
        RECAP_CALL_LOG,
    ),  # when each recap call was made, for the day's tally (#2308)
    ("0206_timeline_stable", TIMELINE_STABLE),  # the timeline door only reads (#2485)
    ("0207_recap_approve_unchecked", RECAP_APPROVE_UNCHECKED),  # approve an unchecked draft (#2305)
    ("0208_web_image_lines", WEB_IMAGE_LINES),  # the website tags a picture line (#2507)
    ("0209_recap_accepted", RECAP_ACCEPTED),  # each approved version of a recap (#2122)
    ("0210_plot_issues", PLOT_ISSUES),  # PlotBot's check of a recap and its issues (#2122)
    ("0211_plot_chat", PLOT_CHAT),  # the DM's talk with PlotBot about an issue (#2123)
    ("0212_handover_email_offers", HANDOVER_EMAIL),  # offers sent by email (#2532)
    ("0213_handover_claim", HANDOVER_CLAIM),  # opening a hand-over link on the website (#2532)
    ("0214_handover_move", HANDOVER_MOVE),  # a hand-over to another server is asked for (#2532)
    ("0215_campaign_move_cascade", CAMPAIGN_MOVE_CASCADE),  # a campaign can change server (#2532)
    ("0216_web_owned_campaigns", WEB_OWNED_CAMPAIGNS),  # the owner's campaigns, any server (#2554)
    ("0217_import_progress", IMPORT_STEPS),  # step counts and last-moved time per import (#2564)
    ("0218_web_delete_notices", WEB_DELETE_NOTICES),  # the bot finishes a website delete (#2554)
    ("0219_web_backups", WEB_BACKUPS),  # the copy of a campaign the website downloads (#2554)
    ("0220_line_topic_choices", TOPIC_CHOICES),  # a DM puts a left-out stretch back (#2567)
    ("0221_recap_story_room", RECAP_STORY_ROOM),  # the recap story column holds 30,000 (#2601)
    ("0222_npc_or_pc", NPC_OR_PC),  # every character is an NPC or a PC: a god is an NPC (#2562)
    ("0223_transcript_erasures", TRANSCRIPT_ERASURES),  # a restore skips erased lines (#2605)
    ("0224_recap_again", RECAP_AGAIN),  # a DM makes or redoes any session's recap (#2608)
    ("0225_recap_uploaded", RECAP_UPLOADED),  # a recap written by the table, kept (#2609)
    ("0226_audio_told", RECAP_AUDIO_TOLD),  # the DM is told once when the audio is done (#2621)
    ("0227_name_pass", NAME_PASS),  # PlotBot repairs names in a finished transcript (#2628)
    (
        "0228_graph_overview",
        GRAPH_OVERVIEW,
    ),  # every name on the Story graph, not only the centre's web (#2632)
    ("0229_link_decide", LINK_DECIDE_ACTIONS),  # Confirm / Not right from the website (#2635)
    ("0230_audio_remake", RECAP_AUDIO_REMAKE),  # what the audio did and how far (#2630)
    ("0231_link_upload", BACKSTORY_LINK_UPLOAD),  # where a waiting connection came from (#2650)
    ("0232_link_which", BACKSTORY_WHICH),  # "Which Mara?" waits for the DM's pick (#2650)
    ("0233_web_waiting", WEB_WAITING),  # the website reads and answers what waits (#2652)
    (
        "0234_move_cascade_late",
        MOVE_CASCADE_LATE,
    ),  # keys of tables made after 0215 cascade too (#2550)
    ("0235_memory_evidence", MEMORY_EVIDENCE),  # typed evidence links with placeholders (#2662)
    ("0236_claim_readings", CLAIM_READINGS),  # a claim can be a reading, a belief to place (#2412)
    ("0237_web_readings", WEB_READINGS),  # the website keeps, refuses and undoes readings (#2412)
    (
        "0238_contradiction_grades",
        CONTRADICTION_GRADES,
    ),  # PlotBot's grade of each candidate (#2660)
    (
        "0239_name_pass_reports",
        NAME_PASS_REPORTS,
    ),  # what the name pass did, for the website (#2684)
    ("0240_names_fixed_door", NAMES_FIXED_DOOR),  # the recap page reads it (#2684)
    ("0241_names_fixed_undo", NAMES_FIXED_UNDO),  # and undoes a fix, queued for the bot (#2684)
    ("0242_name_change_words", NAME_CHANGE_WORDS),  # Change the words on names to check (#2593)
    ("0243_recap_present", RECAP_PRESENT_DOORS),  # who was in each scene, saved by the DM (#2684)
    ("0244_report_storage", REPORT_STORAGE),  # the after-session report's items and answers (#2661)
    ("0245_recap_name_places", RECAP_NAME_PLACES),  # where a name to check was said (#2403)
    (
        "0246_names_read_campaign",
        NAMES_READ_CAMPAIGN,
    ),  # the lines it returns are this campaign's (#2729)
    ("0247_all_names_kinds", ALL_NAMES_KINDS),  # the Names page's type filter takes several kinds
    (
        "0248_memory_vectors",
        MEMORY_VECTORS,
    ),  # meaning search: numbers made from campaign text (#2664)
    ("0249_report_read_door", REPORT_READ_DOOR),  # a session's after-session report (#2661)
    ("0250_report_answer_door", REPORT_ANSWER_DOOR),  # the DM's answer to one item (#2661)
    ("0252_optional_rules_read", OPTIONAL_RULES_READ),  # the Settings tab reads the choices (#2753)
    ("0253_names_smart", NAMES_SMART),  # the Names page's search by meaning (#2733)
    (
        "0254_optional_rule_setting",
        OPTIONAL_RULE_SETTING,
    ),  # a rule switched on the website is queued for the bot (#2753)
    ("0256_plot_chat_followups", PLOT_CHAT_FOLLOWUPS),  # hour_full, Save stamped, timed out (#2545)
    ("0257_game_masters_read", GAME_MASTERS_READ),  # a campaign's game masters, and who to offer
    ("0258_web_backup_parts", WEB_BACKUP_PARTS),  # a website copy carries its transcripts (#2772)
    ("0259_co_dm_setting", GAME_MASTER_SETTING),  # a game master added or removed is queued (#2768)
    ("0261_rule_sharing_read", RULE_SHARING_READ),  # a campaign's shared and followed rules (#2783)
    ("0262_web_rule_actions", WEB_RULE_ACTIONS),  # sharing asked for on the website (#2783)
    ("0263_unnamed_people", UNNAMED_PEOPLE),  # a person described but never named (#2790)
    # the doors and search text that keep the DM's entries from players keep these too (#2790)
    ("0264_unnamed_hidden_from_players", UNNAMED_HIDDEN_FROM_PLAYERS),
    (
        "0265_web_report_requests",
        WEB_REPORT_REQUESTS,
    ),  # make the report again, on the website (#2661)
    ("0266_web_name_unnamed", WEB_NAME_UNNAMED),  # "Not named yet" on the Names page (#2803)
    ("0267_who_is_it_decided", BACKSTORY_DECIDED),  # how a pronoun was settled (#2790)
    (
        "0268_web_sheet_actions",
        WEB_SHEET_ACTIONS,
    ),  # a character sheet asked for on the website (#2816)
    (
        "0269_report_fixed_remind",
        REPORT_FIXED_REMIND,
    ),  # Undo of a fix gives a reminder back (#2661)
    ("0271_web_restores", WEB_RESTORES),  # a backup loaded from the website (#2745)
)

# Tables that must have row-level security. A test checks every table in the schema
# except these is listed here, so a new table can't forget its policy.
ISOLATED_TABLES = (
    "memory_differences",
    "report_answers",
    "memory_report_runs",
    "memory_vectors",
    "memory_contradictions",
    "name_pass_reports",
    "name_pass_fixes",
    "memory_evidence",
    "recap_upload_rights",
    "plot_chat",
    "recap_accepted",
    "plot_checks",
    "plot_issues",
    "plot_fine",
    "backstory_which",
    "transcript_erasures",
    "secret_exposures",
    "web_delete_notices",
    "web_backups",
    "web_backup_parts",
    "merge_restores",
    "transcript_respells",
    "image_day_use",
    "map_notes",
    "test_voice_consent",
    "campaigns",
    "campaign_dms",
    "campaign_optional_rules",
    "consent",
    "active_sessions",
    *_MEMORY_TABLES,
    "transcript_sessions",
    "transcript_lines",
    "session_imports",
    "import_speakers",
    "import_plays",
    "house_rule_wins",
    "member_directory",
    "transcript_suggestions",
    "waiting_name_questions",
    "memory_heard",
    "said_as_suggestions",
    "shared_confirmations",
    "campaign_handover_offers",
    "character_sheets",
    "house_rules",
    "house_rules_file",
    "homebrew_entries",
    "session_usage",
    "game_clocks",
    "game_clock_groups",
    "game_effects",
    "recap_jobs",
    "recap_dictionaries",
    "recap_holds",
    "name_later",
    "backstory_links",
    "memory_rule_links",
    "fightbot_players",
    "rule_sets",
    "rule_set_versions",
    "rule_set_codes",
    "rule_set_follows",
    "rule_set_editors",
    "memory_states",
    "memory_impressions",
    "memory_attitudes",
    "memory_perspectives",
    "memory_beliefs",
    "memory_claims",
    "rule_set_suggestions",
    "rule_set_suggestions_sent",
    "pack_suggestions",
    "pack_suggestions_sent",
    "site_approvals",
    "adventure_documents",
    "adventure_chunks",
    "adventure_reads",
    "plan_nodes",
    "plan_links",
    "plan_spend",
    "plan_same_as",
    "plan_divergences",
    "plan_position",
    "npcs_channel_messages",
    "npc_roster",
    "npc_notes",
    "recap_style_examples",
    "recap_style_notes",
    "recap_requests",
    "recap_name_checks",
    "name_not_same",
    "name_answers",
    "name_actions",
    "setting_actions",
    "web_rule_actions",
    "web_report_requests",
    "web_sheet_actions",
    "web_peeks",
    "web_restores",
    "web_restore_parts",
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
    "email_changes",
    "installs",
    # Every Discord account a DMbot account used; keyed by its DMbot ID (#1230).
    "account_id_history",
    # Keyed by Discord account (#771): the person's own grant, or the grant writer.
    "access_grants",
    # Not per person: only the grant writer adds or reads a row (#771).
    "access_log",
    # A person adds their own ticket and reads back only their own list, never the words; the
    # support admin door reads everything (#1340, #1373).
    "support_tickets",
    # The replies of a ticket: only the admin door adds or reads them (#1431).
    "support_replies",
    # The week the weekly support email last went; only the admin door reads or writes it (#1514).
    "support_week_mail",
    # The IDs of the emails sent for a ticket; only the admin door reads or writes them (#2192).
    "support_mail_ids",
    # An owner's hours per plan month; only the meter adds to it (#437).
    "owner_hours",
    # Which campaigns an owner has, across servers; kept by a trigger on campaigns (#437).
    "owner_campaigns",
    # A person's choice not to have their words used to improve DMbot (#1609).
    "improvement_opt_outs",
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
    "recap_audio_strays",
    "move_code_asks",
    # One copy of each published homebrew page, whoever imported it (#1633): public content with
    # no campaign scope, the second exception to campaign isolation. No grant for the website.
    "homebrew_pages",
    *ROUTING_TABLES,
)
