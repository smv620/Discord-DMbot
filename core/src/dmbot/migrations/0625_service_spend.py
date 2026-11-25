"""0625_service_spend

What DMbot's paid calls cost, as business totals (#3672, the Costs page's part 2).
"""

from __future__ import annotations

from dmbot.migrations._shared import _setting

# One row for each month, service, model, feature, plan and source, summed by an upsert (never one
# row per call): the totals the admin page shows (cost by plan, by service, by feature) need
# nothing more. Deliberately NO guild, campaign, person or word on a row (design note on #3672,
# ruled yes): it holds no personal data, so it is not on a campaign's backup or move list, and the
# history stays when a campaign is deleted. `feature` and `model` are fixed words from the code
# (the AI feature's name, a model id), a length limit only so a new feature needs no migration.
# `plan` is the campaign owner's plan when the call was made, or `free`, `listed` (a free-list
# person or server) or `unknown` (a call with no campaign). `source` is `sample` for a test run, so
# its spend is never mixed with real play. `refused`: calls refused for limits or billing
# (#3672, last night's case); `unpriced`: calls recorded at the high default for want of a price.
# Two per-transaction settings open it, as the support tickets' admin door does (0074): the bot
# sets `dmbot.spend` to `write` (add to a total), the admin page's API to `admin` (read them);
# the website's role could set either itself, so which code opens them is a Python boundary
# (`tests/test_service_spend.py` checks).
SQL = (
    _setting("dmbot_spend", "dmbot.spend", "TEXT")
    + r"""
    CREATE TABLE service_spend (
        month_start BIGINT NOT NULL,
        service     TEXT NOT NULL
            CHECK (service IN ('anthropic', 'gemini', 'deepgram', 'elevenlabs', 'voyage')),
        model       TEXT NOT NULL CHECK (char_length(model) <= 60),
        feature     TEXT NOT NULL CHECK (char_length(feature) <= 40),
        plan        TEXT NOT NULL CHECK (char_length(plan) BETWEEN 1 AND 20),
        source      TEXT NOT NULL CHECK (source IN ('play', 'sample')),
        calls       BIGINT NOT NULL DEFAULT 0 CHECK (calls >= 0),
        units_in    BIGINT NOT NULL DEFAULT 0 CHECK (units_in >= 0),
        units_out   BIGINT NOT NULL DEFAULT 0 CHECK (units_out >= 0),
        micro_usd   BIGINT NOT NULL DEFAULT 0 CHECK (micro_usd >= 0),
        refused     BIGINT NOT NULL DEFAULT 0 CHECK (refused >= 0),
        unpriced    BIGINT NOT NULL DEFAULT 0 CHECK (unpriced >= 0),
        PRIMARY KEY (month_start, service, model, feature, plan, source)
    );
    ALTER TABLE service_spend ENABLE ROW LEVEL SECURITY;
    ALTER TABLE service_spend FORCE ROW LEVEL SECURITY;
    -- The bot adds to a total (the upsert reads the row it adds to); only the admin page reads.
    CREATE POLICY spend_write ON service_spend FOR INSERT WITH CHECK (dmbot_spend() = 'write');
    CREATE POLICY spend_update ON service_spend FOR UPDATE
        USING (dmbot_spend() = 'write') WITH CHECK (dmbot_spend() = 'write');
    CREATE POLICY spend_read ON service_spend FOR SELECT
        USING (dmbot_spend() IN ('admin', 'write'));
    """
)

# (row-level security is on, by the setting above and not by a server; the catalog lists it)
ISOLATED_TABLES: tuple[str, ...] = ("service_spend",)
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
