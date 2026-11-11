"""0422_tie_end_reason

How an item changed hands (#4115; Supervisor's ruling and the owner's addition, 2026-11-10): why a
holder's tie to an item ended, and the DM's "Yes, both have it" answers.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- Why an `owns` or `carries` ended (#4115): 'sold', 'gave', 'traded', 'stolen' or 'lost', or
    -- NULL when the story didn't say (the DM's plain "No, not any more", an end from before this).
    -- Only on an ended tie: the store clears it whenever a tie is reopened (an Undo puts the old
    -- row back whole). Only on those two: "found" and every other event-like tie never end, so they
    -- never have a reason. No existing row changes (the default is NULL), so no row-level security
    -- needs opening. Carried in the change log, merges (the row is kept) and backups.
    ALTER TABLE memory_relations
        ADD COLUMN end_reason TEXT
            CHECK (end_reason IN ('sold', 'gave', 'traded', 'stolen', 'lost'))
            CHECK (end_reason IS NULL OR predicate IN ('owns', 'carries'))
            CHECK (end_reason IS NULL OR to_session_at IS NOT NULL);

    -- The DM's "Yes, both have it" (#4115): two holders of a one-of-a-kind item that DMbot must not
    -- ask about again. Ids and a time only. The two holders are sorted, so one row is the pair
    -- either way round; a holder or the item deleted takes the row with it. Not in a backup (it is
    -- an answer to a question: a restored campaign may ask it once more, `campaigns/tables.py`).
    CREATE TABLE memory_hand_over_both (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        item_id        TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, item_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        first_id       TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, first_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        second_id      TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, second_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_hand_over_both_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, item_id, first_id, second_id),
        CHECK (first_id < second_id)  -- (one order for a pair: sorted)
    );
    -- (a deleted or renumbered entry finds its rows by index: every memory link has one)
    CREATE INDEX memory_hand_over_both_first
        ON memory_hand_over_both (guild_id, campaign_id, first_id);
    CREATE INDEX memory_hand_over_both_second
        ON memory_hand_over_both (guild_id, campaign_id, second_id);
    CREATE TRIGGER memory_hand_over_both_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_hand_over_both
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_hand_over_both")

ISOLATED_TABLES: tuple[str, ...] = ("memory_hand_over_both",)
