"""0550_party_join_questions

The DM's question when a character joins the party (#4537, party knowledge slice 2b;
`docs/design/party_knowledge.md` §5, Supervisor's ruling on #4537, 2026-11-18): "Mira has joined
the party. What does Mira know about what came before?"
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row per character who joined a party that already had members (a campaign's first characters
# are asked nothing). Characters who join before their question is sent share one message: the
# send claims them together under one `group_id`, and the buttons carry that group.
# - `status`: `waiting` until the DM answers; `everyone` ("Only what everyone knows": nothing is
#   written); `party` ("What the party knows": the copy made in `answered_batch`); `first` (the DM
#   said the character was there from the first session, which copies the same and moves its
#   stretch, in `answered_batch`). An Undo of `answered_batch` puts `party` back to `everyone` and
#   `first` back to `waiting` (`memory/join_questions.undo`).
# - `batch`: the change that made the character join, whose Undo takes a still-waiting question.
# - `sent_at`: when it was claimed for sending, so it goes once; never sent during play.
# - A character has at most one waiting question (the unique index). It goes with its character
#   (`ON DELETE CASCADE`) and follows a campaign moved to another server (`ON UPDATE CASCADE`,
#   #2550). Not in a backup: a restored campaign starts with no question waiting (its DM tells a
#   character more from any card). Ids and times only, never a word of the story.
SQL = r"""
    CREATE TABLE memory_join_questions (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        entity_id       TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'waiting'
            CHECK (status IN ('waiting', 'everyone', 'party', 'first')),
        batch           BIGINT,
        group_id        TEXT CHECK (group_id ~ '^[0-9a-f]{32}$'),
        sent_at         BIGINT,
        answered_at     BIGINT,
        answered_batch  BIGINT,
        created_at      BIGINT NOT NULL,
        made_by_commit  TEXT CONSTRAINT memory_join_questions_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((group_id IS NULL) = (sent_at IS NULL)),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX memory_join_questions_one_waiting
        ON memory_join_questions (guild_id, campaign_id, entity_id) WHERE status = 'waiting';
    CREATE INDEX memory_join_questions_by_group
        ON memory_join_questions (guild_id, campaign_id, group_id);
    CREATE TRIGGER memory_join_questions_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_join_questions
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_join_questions")

ISOLATED_TABLES: tuple[str, ...] = ("memory_join_questions",)
