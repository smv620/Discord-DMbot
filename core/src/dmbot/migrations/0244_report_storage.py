"""0244_report_storage

The after-session report's items and answers (#2661).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

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

SQL = REPORT_STORAGE

ISOLATED_TABLES: tuple[str, ...] = (
    "memory_differences",
    "report_answers",
    "memory_report_runs",
)
