"""0510_secret_questions

The 🤫 questions DMbot asks instead of taking knowledge away from the players by itself (#4524, the
owner's rule "public never becomes secret by itself"; Supervisor's ruling on #4524, slice 1).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row a question. A write that is not the DM's own choice (`Changes.dm_chose_secret`) and
# would hide something the players already know, make it secret, or take a player character off
# who knows it, keeps it as it is and asks instead (`memory.public_guard`):
# - `kind`: 'narrow' ("This sounds like a secret, but the players already know it: Keep it known /
#   Make it secret") or 'replace' (a backstory connection that would end a public one: "Did this
#   change? Keep both / End the old one").
# - `item_table` and `item_id`: the entry, other name or connection it is about (ids only). No
#   foreign key, as the change log's rows have none: the item is looked up when the card is drawn,
#   and a question whose item is gone shows as answered.
# - `change`: what the write would have done, as column values or the knowers it would have taken
#   off ({"hidden_from_players": true}, {"secret": true}, {"knowers_off": [<point of view ids>]},
#   {"end": true, "by": <the new connection>}); `change_key` is it in a fixed form, so the same
#   change on the same item is asked once, whatever became of the first question (Keep it known is
#   remembered, the ruling's point 2). A different change may still ask.
# - Evidence: `label` (an upload's or a session's label) and, from a session, `session_id`,
#   `line_starts` and `speaker_ids` (5a's consent rule: no words; a waiting question goes when a
#   speaker it quotes stops recording, `secret_questions.forget`).
# - `status`: 'waiting', 'kept_known' (Keep it known / Keep both) or 'made_secret' (Make it secret
#   / End the old one, which is the DM's own change with its own ↩ Undo).
# Written through the change log (`_changes.SECRET_QUESTIONS`), so an upload's Undo takes back the
# questions it asked with the rest of it. Per campaign with forced row-level security; it follows
# a campaign moved to another server and goes with it. Left out of backups as a wait
# (`campaigns/tables.py`). No existing row changes.
SQL = r"""
    CREATE TABLE secret_questions (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('narrow', 'replace')),
        item_table   TEXT NOT NULL
            CHECK (item_table IN ('memory_entities', 'memory_aliases', 'memory_relations')),
        item_id      TEXT NOT NULL CHECK (char_length(item_id) <= 64),
        change       JSONB NOT NULL CHECK (jsonb_typeof(change) = 'object'),
        change_key   TEXT NOT NULL CHECK (char_length(change_key) <= 400),
        label        TEXT NOT NULL DEFAULT '' CHECK (char_length(label) <= 200),
        session_id   TEXT CHECK (session_id IS NULL OR char_length(session_id) <= 64),
        line_starts  BIGINT[] NOT NULL DEFAULT '{}',
        speaker_ids  BIGINT[] NOT NULL DEFAULT '{}',
        status       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (status IN ('waiting', 'kept_known', 'made_secret')),
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, item_table, item_id, change_key)
    );
    CREATE INDEX secret_questions_waiting ON secret_questions (guild_id, campaign_id)
        WHERE status = 'waiting';
    CREATE INDEX secret_questions_by_speaker ON secret_questions USING gin (speaker_ids);
    """ + _isolate("secret_questions")

ISOLATED_TABLES: tuple[str, ...] = ("secret_questions",)
