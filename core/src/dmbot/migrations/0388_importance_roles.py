"""0388_importance_roles

Story roles apart from why a rank changed (#3172, item b, owner-approved 2026-11-01).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The owner asked what "corrected" said about a character (nothing: it said a row was updated) and
# how the graph knows WHY someone is a quest giver. So the one `basis` code of 0312 splits in two:
#
# - `memory_importance.reason`: why a rank changed, a property of the assessment (`reading`,
#   `dm_corrected`, `revealed`, `demoted`, `rule`). Old rows map `corrected` to `dm_corrected` and
#   every other old code to `reading`, as ruled; `background`, `met_a_pc` and `recurring` were rank
#   levels, not reasons, and the rank itself is kept.
# - story roles: what an entry IS in the story, several at once. A role a connection shows (a
#   mentor of a player character) is worked out on read and never stored; the rest (a hidden
#   villain, a god, a plot artifact) are rows of `memory_roles`, with the session and line numbers
#   that showed it and no word of the story. `ended_at` closes one (append-only, like the graph).
# - `memory_role_codes`: the campaign's list of roles a row may name, so a campaign's own role
#   (the reading may add up to 40) never crosses campaigns. The owner's 29 stored seeded roles
#   (commoner never is) are rows here too (code and label; their descriptions live in code,
#   `memory/roles.py`), seeded for every existing campaign below and for a new one before its
#   first role is written (`role_store.ensure_role_codes`). An added role carries its
#   description, examples and the role it is not, which is what the embedding match reads later.
#
# Both tables are DM-only (no web role reads them) and in the campaign's backup. Rows are written
# by readers, so both carry the commit stamp (#3739). The backfill opens row-level security on the
# two tables it touches and closes it again (CLAUDE.md, #704).
# The owner's seeded roles as of this migration (`memory/roles.py` `STORED_SEEDED`; commoner is
# never stored). Written out, so a later change to the list never changes what this migration did.
_SEED_CODES = (
    "quest_giver",
    "quest_target",
    "antagonist",
    "mini_boss",
    "minion",
    "rival",
    "spy",
    "scapegoat",
    "rebel",
    "tempter",
    "companion",
    "sidekick",
    "mentor",
    "ally",
    "love_interest",
    "informant",
    "captive",
    "herald",
    "prophet",
    "witness",
    "martyr",
    "comic_relief",
    "ruler",
    "god",
    "lore_figure",
    "worker",
    "plot_artifact",
    "key_location",
    "central_event",
)
_SEEDED = ", ".join(f"('{code}')" for code in _SEED_CODES)

SQL = (
    r"""
    CREATE TABLE memory_role_codes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        code        TEXT NOT NULL
                    CHECK (code ~ '^[a-z]+(_[a-z]+){0,3}$' AND char_length(code) <= 32),
        label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 60),
        description TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 1200),
        examples    TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(examples) <= 6),
        not_this    TEXT NOT NULL DEFAULT '' CHECK (char_length(not_this) <= 240),
        source      TEXT NOT NULL CHECK (source IN ('seeded', 'reading')),
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_role_codes_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, code)
    );
    CREATE TRIGGER memory_role_codes_stamp_commit BEFORE INSERT OR UPDATE ON memory_role_codes
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();

    CREATE TABLE memory_roles (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        entity_id          TEXT NOT NULL,
        role               TEXT NOT NULL,
        reason             TEXT NOT NULL
                           CHECK (reason IN ('reading', 'dm_corrected', 'revealed', 'rule')),
        session_started_at BIGINT,
        lines              INTEGER[] NOT NULL DEFAULT '{}' CHECK (cardinality(lines) <= 20),
        recorded_at        BIGINT NOT NULL CHECK (recorded_at > 0),
        ended_at           BIGINT CHECK (ended_at IS NULL OR ended_at >= recorded_at),
        made_by_commit     TEXT CONSTRAINT memory_roles_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, role)
            REFERENCES memory_role_codes (guild_id, campaign_id, code)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (one open row per entry and role: a role is held or not, and a re-reading adds nothing)
    CREATE UNIQUE INDEX memory_roles_open
        ON memory_roles (guild_id, campaign_id, entity_id, role) WHERE ended_at IS NULL;
    -- (every row, ended ones too, for the two links a delete or a move follows)
    CREATE INDEX memory_roles_by_entity ON memory_roles (guild_id, campaign_id, entity_id);
    CREATE INDEX memory_roles_by_role ON memory_roles (guild_id, campaign_id, role);
    CREATE TRIGGER memory_roles_stamp_commit BEFORE INSERT OR UPDATE ON memory_roles
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """
    + _isolate("memory_role_codes")
    + _isolate("memory_roles")
    + r"""
    ALTER TABLE memory_importance ADD COLUMN reason TEXT;
    CREATE POLICY migrate_backfill ON memory_importance FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_importance
    SET reason = CASE WHEN basis = 'corrected' THEN 'dm_corrected' ELSE 'reading' END;
    DROP POLICY migrate_backfill ON memory_importance;
    ALTER TABLE memory_importance ALTER COLUMN reason SET NOT NULL;
    ALTER TABLE memory_importance ADD CONSTRAINT memory_importance_reason_check
        CHECK (reason IN ('reading', 'dm_corrected', 'revealed', 'demoted', 'rule'));
    ALTER TABLE memory_importance DROP COLUMN basis;

    CREATE POLICY migrate_backfill ON campaigns FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_role_codes FOR ALL USING (true) WITH CHECK (true);
    INSERT INTO memory_role_codes (guild_id, campaign_id, code, label, source, created_at)
    SELECT c.guild_id, c.id, s.code, replace(s.code, '_', ' '), 'seeded',
           extract(epoch FROM now())::BIGINT
    FROM campaigns c CROSS JOIN (VALUES """
    + _SEEDED
    + r""") AS s(code)
    ON CONFLICT DO NOTHING;
    DROP POLICY migrate_backfill ON memory_role_codes;
    DROP POLICY migrate_backfill ON campaigns;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("memory_role_codes", "memory_roles")

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
