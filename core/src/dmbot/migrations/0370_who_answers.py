"""0370_who_answers

The AI's ranked answers for a name waiting on "Who is this?" (#3602).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

WHO_ANSWERS = r"""
    -- What the AI thinks a waiting name is (#3602, slice 2): up to three ranked answers for an
    -- entry waiting for the DM, each either an entry already in the story or "new" with a kind,
    -- with a confidence word (never a number). DM-only everywhere: no player door reads this table.
    -- Made again each session, so it is not carried in a backup (campaigns/tables.py). The
    -- answers hold ids and words DMbot chose, never a line of a transcript. `speaker_ids` are the
    -- people whose lines the question was worked out from: the answers go when any of them stops
    -- being recorded. It is kept only while the question is open (a sweep deletes the answers once
    -- the entry is named, joined, removed or decided, or the entry they name has gone), so there is
    -- no foreign key to either entry. The campaign's foreign key moves with a campaign
    -- (ON UPDATE CASCADE).
    CREATE TABLE memory_who_answers (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id          TEXT NOT NULL CHECK (entity_id ~ '^[0-9a-f]{32}$'),
        rank               SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 3),
        pick               TEXT NOT NULL CHECK (pick IN ('entity', 'new')),
        target_id          TEXT CHECK (target_id ~ '^[0-9a-f]{32}$'),
        new_kind           TEXT CHECK (new_kind IN (
                               'character', 'place', 'faction', 'item', 'event')),
        confidence         TEXT NOT NULL CHECK (confidence IN ('sure', 'likely', 'maybe')),
        speaker_ids        BIGINT[] NOT NULL,
        session_started_at BIGINT,
        prompt_version     TEXT NOT NULL CHECK (char_length(prompt_version) BETWEEN 1 AND 40),
        model              TEXT NOT NULL CHECK (char_length(model) <= 80),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, entity_id, rank),
        CHECK ((pick = 'entity' AND target_id IS NOT NULL AND new_kind IS NULL)
            OR (pick = 'new' AND target_id IS NULL AND new_kind IS NOT NULL))
    );
    """ + _isolate("memory_who_answers")

SQL = WHO_ANSWERS

ISOLATED_TABLES: tuple[str, ...] = ("memory_who_answers",)
