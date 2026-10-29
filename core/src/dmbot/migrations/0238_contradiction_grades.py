"""0238_contradiction_grades

PlotBot's grade of each candidate (#2660).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

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

SQL = CONTRADICTION_GRADES

ISOLATED_TABLES: tuple[str, ...] = ("memory_contradictions",)
