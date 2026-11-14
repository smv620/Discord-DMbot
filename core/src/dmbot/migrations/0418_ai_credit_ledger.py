"""0418_ai_credit_ledger

The AI credits ledger (#4005, docs/AI_CREDITS_DESIGN.md): what each paid AI call cost, in credits,
and each account's total for the plan month. It only measures: nothing reads it to refuse anything
yet.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `ai_credit_spend` is one row per AI call made because a DM added or processed material: counts
# only, never a word of the text, a name or a file name. It is a ledger like `plan_spend`: it is not
# tied to a document or an upload, so removing and re-adding one cannot get round the meter. It goes
# with the campaign and is NOT in a backup (a restored campaign starts its month fresh).
# `owner_user_id` is 0 when nobody owned the campaign yet; those rows never reach `owner_credits`.
# `owner_credits` is the account's month total, summed over every campaign they own, like
# `owner_hours` (0032): only the bot's meter adds to it, and the owner may read their own row.
# BIGINT from the start (#4060): a sum must never overflow a 32-bit column.
SQL = (
    r"""
    CREATE TABLE ai_credit_spend (
        guild_id      BIGINT NOT NULL,
        campaign_id   TEXT NOT NULL,
        id            TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id >= 0),
        job_kind      TEXT NOT NULL CHECK (char_length(job_kind) BETWEEN 1 AND 40),
        feature       TEXT NOT NULL CHECK (char_length(feature) <= 40),
        credits       BIGINT NOT NULL CHECK (credits >= 0),
        month_start   BIGINT NOT NULL,
        at_ms         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX ai_credit_spend_month ON ai_credit_spend (guild_id, campaign_id, month_start);
    """
    + _isolate("ai_credit_spend")
    + r"""
    CREATE TABLE owner_credits (
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id > 0),
        month_start   BIGINT NOT NULL,
        credits       BIGINT NOT NULL DEFAULT 0 CHECK (credits >= 0),
        PRIMARY KEY (owner_user_id, month_start)
    );
    ALTER TABLE owner_credits ENABLE ROW LEVEL SECURITY;
    ALTER TABLE owner_credits FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON owner_credits FOR SELECT
        USING (owner_user_id = dmbot_current_user());
    CREATE POLICY meter_insert ON owner_credits FOR INSERT
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    CREATE POLICY meter_update ON owner_credits FOR UPDATE
        USING (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot')
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("ai_credit_spend",)

USER_ISOLATED_TABLES: tuple[str, ...] = ("owner_credits",)
