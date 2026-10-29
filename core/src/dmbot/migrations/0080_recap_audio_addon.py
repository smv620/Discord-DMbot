"""0080_recap_audio_addon

The recap audio add-on (#1389).
"""

from __future__ import annotations

RECAP_AUDIO_ADDON = """
    -- The recap audio add-on (#1389; owner decision on #1381): a second, monthly subscription
    -- that adds spoken and musical recaps to a paid plan that does not include them (Guild and
    -- Pro do). Like extra hours it is a row on the account, written only by the payment writer
    -- (the guard on `entitlements` already allows nothing else).
    --   recap_audio_until            the end of the period paid for; the add-on works until then
    --   recap_audio_subscription_id  the company's subscription, so it can be cancelled with the
    --                                plan or the account
    --   recap_audio_event_at         the newest add-on event applied, so a late one is ignored
    ALTER TABLE entitlements ADD COLUMN recap_audio_until BIGINT;
    ALTER TABLE entitlements ADD COLUMN recap_audio_subscription_id TEXT;
    ALTER TABLE entitlements ADD COLUMN recap_audio_event_at BIGINT NOT NULL DEFAULT 0;
    """

SQL = RECAP_AUDIO_ADDON
