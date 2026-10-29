"""0043_test_voice_consent."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

TEST_VOICE_CONSENT = """
    -- Test recordings (#1019; docs/PLAN.md, "Test recordings"): the second yes, "Save my voice
    -- for tests", given in a server listed in DMBOT_TEST_RECORDING_GUILDS. Separate from the
    -- recording consent: without it, nothing of the person is saved. Per server, only a
    -- date: the files themselves are never in the database.
    CREATE TABLE test_voice_consent (
        guild_id   BIGINT NOT NULL,
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        granted_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """ + _isolate("test_voice_consent")

SQL = TEST_VOICE_CONSENT

ISOLATED_TABLES: tuple[str, ...] = ("test_voice_consent",)
