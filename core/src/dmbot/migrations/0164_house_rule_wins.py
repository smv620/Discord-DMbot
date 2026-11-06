"""0164_house_rule_wins

Which of two disagreeing rules wins (#2284).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
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

SQL = HOUSE_RULE_WINS

ISOLATED_TABLES: tuple[str, ...] = ("house_rule_wins",)
