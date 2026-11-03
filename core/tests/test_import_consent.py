"""Importing with the uploader's consent per speaker (#3305): a member who has not agreed can be
ticked in, a member who said no never can (checked in the database too, whatever the page sent), a
speaker without Discord is kept by name under a negative id, and every tick leaves its record.
Invented fixtures only."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from dmbot.consent import TERMS_VERSION, ConsentStore
from dmbot.transcript import uploader_words as uw
from dmbot.web import imports
from dmbot.web.recaps import RecapError, _scoped
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_imports import (
    CRAIG,
    MAPPING,
    MIRA,
    TOMAS,
    WithImports,
)

SESSION = [{"file": "a.txt", "date": "2026-09-14", "order": 1}]
GHOST = "Old Greg (greg)"  # a speaker not on Discord
WITH_GHOST = CRAIG + "Old Greg (greg): I hold the rope, said Greg.\n"


def plan(mapping: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"mapping": mapping, "sessions": SESSION, **extra}


class Confirming(WithImports):
    async def lines(self) -> list[tuple[int, str]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT l.user_id, l.heard FROM transcript_lines l"
                " JOIN transcript_sessions s ON s.id = l.session_id AND s.guild_id = l.guild_id"
                " WHERE l.guild_id = %s AND s.campaign_id = %s ORDER BY l.started_ms, l.id",
                (THURSDAY.id, self.one.id),
            )
            return [(int(r["user_id"]), str(r["heard"])) for r in await cur.fetchall()]

    async def rows(self, table: str, campaign: str | None = None) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                f"SELECT * FROM {table} WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, campaign or self.one.id),
            )
            return list(await cur.fetchall())

    async def test_a_member_who_has_not_agreed_is_kept_when_the_uploader_ticks(self) -> None:
        mapping: dict[str, Any] = {
            "Mira (mira_v)": str(MIRA),
            "Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)},
        }
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        who = {user for user, _ in await self.lines()}
        self.assertEqual(who, {MIRA, TOMAS})
        [record] = await self.rows("import_consents")
        self.assertEqual(
            (record["user_id"], record["basis"], record["confirmed_by"], record["terms_version"]),
            (TOMAS, "uploader_confirmed", ALICE.id, TERMS_VERSION),
        )
        self.assertEqual(record["lines"], 1)
        self.assertGreater(record["confirmed_at"], 0)

    async def test_a_member_who_agreed_anyway_leaves_no_record(self) -> None:
        mapping: dict[str, Any] = {"Mira (mira_v)": {"confirmed": True, "member": str(MIRA)}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        self.assertEqual(await self.rows("import_consents"), [])

    async def test_an_unticked_member_who_has_not_agreed_still_stores_nothing(self) -> None:
        # (as before: a plain mapping to someone who has not agreed keeps none of their lines)
        await self.confirm(plan(MAPPING), ("a.txt", CRAIG))
        self.assertEqual({user for user, _ in await self.lines()}, {MIRA})
        self.assertEqual(await self.rows("import_consents"), [])

    async def test_a_member_who_said_no_cannot_be_ticked_in(self) -> None:
        consent = ConsentStore(self.db)
        await consent.grant(THURSDAY.id, TOMAS)
        await consent.revoke(THURSDAY.id, TOMAS)  # "Stop recording me"
        mapping: dict[str, Any] = {"Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)}}
        with self.assertRaises(RecapError) as raised:
            await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        self.assertEqual(str(raised.exception), "blocked")
        self.assertEqual(await self.lines(), [])
        self.assertEqual(await self.rows("import_consents"), [])

    async def test_the_database_refuses_even_when_the_page_is_bypassed(self) -> None:
        consent = ConsentStore(self.db)
        await consent.revoke(THURSDAY.id, TOMAS)  # turned DMbot down without ever agreeing
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, THURSDAY.id)
            cur = await conn.execute(
                "SELECT dmbot_import_store(%s, %s, 'x', 'a.txt', 1000, %s::jsonb, %s, NULL,"
                " %s::jsonb, %s) AS r",
                (
                    THURSDAY.id,
                    self.one.id,
                    f'[{{"ms": 0, "user": {TOMAS}, "text": "hello", "dur": 10}}]',
                    TERMS_VERSION,
                    f'["{TOMAS}"]',
                    TERMS_VERSION,
                ),
            )
            self.assertEqual((await cur.fetchone() or {}).get("r"), {"error": "blocked"})
        self.assertEqual(await self.lines(), [])

    async def test_a_refusal_is_remembered_by_kind_and_cleared_by_a_yes(self) -> None:
        consent = ConsentStore(self.db)
        await consent.revoke(THURSDAY.id, NOBODY_ID)  # never agreed
        await consent.grant(THURSDAY.id, TOMAS)
        await consent.revoke(THURSDAY.id, TOMAS)  # had agreed
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id, kind FROM consent_refusals WHERE guild_id = %s",
                (THURSDAY.id,),
            )
            got = {int(r["user_id"]): str(r["kind"]) for r in await cur.fetchall()}
        self.assertEqual(got, {NOBODY_ID: "declined", TOMAS: "stopped"})
        await consent.grant(THURSDAY.id, TOMAS)  # said yes again: the no no longer stands
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM consent_refusals WHERE guild_id = %s AND user_id = %s",
                (THURSDAY.id, TOMAS),
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_speaker_without_discord_is_kept_by_name_under_a_negative_id(self) -> None:
        mapping: dict[str, Any] = {
            "Mira (mira_v)": str(MIRA),
            GHOST: {"confirmed": True},
        }
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [speaker] = await self.rows("campaign_speakers")
        self.assertLess(speaker["user_id"], 0)
        self.assertEqual(speaker["name"], "Old Greg")
        self.assertIn((speaker["user_id"], "I hold the rope, said Greg."), await self.lines())
        [record] = await self.rows("import_consents")
        self.assertEqual((record["user_id"], record["lines"]), (speaker["user_id"], 1))

    async def test_a_speaker_without_discord_is_named_in_the_session_and_its_transcript(
        self,
    ) -> None:
        from dmbot.transcript.export import render

        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [speaker] = await self.rows("campaign_speakers")
        ghost = int(speaker["user_id"])
        [session] = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        self.assertEqual(session.speaker_names.get(ghost), "Old Greg")
        # Nothing for the start-up fill-in to ask Discord about: a negative id is never looked up.
        self.assertEqual(
            [m for _, ids in await self.transcripts.unnamed(THURSDAY.id) for m in ids if m < 0], []
        )
        lines = await self.transcripts.lines(THURSDAY.id, session.id)
        text = render("One", session, lines, session.speaker_names)
        self.assertIn("Old Greg", text)
        self.assertNotIn(f"<@{ghost}>", text)

    async def test_the_consent_words_come_with_their_version(self) -> None:
        got = imports.consent_words(cloud=True, company="Deepgram")
        self.assertEqual(got["version"], TERMS_VERSION)
        self.assertIn("Anyone in this server can read and download that text", got["text"])
        self.assertIn("Deepgram", got["text"])  # (and what the server sends out, as in Discord)

    async def test_the_speakers_list_has_names_and_line_counts_for_a_dm_only(self) -> None:
        from typing import cast

        from dmbot.web import sessions as web_sessions
        from tests.test_recap_web import BOB, NOW

        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [speaker] = await self.rows("campaign_speakers")
        got = await imports.speakers(self.web, self.session, self.ref_c, **self.kw)
        self.assertEqual(
            got, {"speakers": [{"id": str(speaker["user_id"]), "name": "Old Greg", "lines": 1}]}
        )
        token = await web_sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await web_sessions.find(self.web, token, now=NOW)
        assert bob is not None  # not a DM of this campaign
        with self.assertRaises(RecapError):
            await imports.speakers(self.web, bob, self.ref_c, **self.kw)

    async def test_the_website_may_write_only_the_signed_in_persons_own_refusal(self) -> None:
        import psycopg

        consent = ConsentStore(self.db)
        await consent.revoke(THURSDAY.id, TOMAS)  # someone else's no, made by the bot
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, THURSDAY.id)
            cur = await conn.execute("SELECT user_id FROM consent_refusals")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [])  # (not theirs)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():  # (a no written for another person)
                    await conn.execute(
                        "INSERT INTO consent_refusals (guild_id, user_id, kind, refused_at)"
                        " VALUES (%s, %s, 'stopped', 1)",
                        (THURSDAY.id, MIRA),
                    )
        # Their own, the way the website's Stop route does it (as the signed-in person).
        scoped = self.web.as_person(self.session.user_id, self.session.id_hash)
        await ConsentStore(scoped).revoke(THURSDAY.id, self.session.user_id)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id FROM consent_refusals WHERE guild_id = %s ORDER BY user_id",
                (THURSDAY.id,),
            )
            got = [int(r["user_id"]) for r in await cur.fetchall()]
        self.assertEqual(got, sorted([TOMAS, self.session.user_id]))

    async def test_the_same_name_in_the_next_file_is_the_same_speaker(self) -> None:
        mapping: dict[str, Any] = {GHOST: {"confirmed": True}}
        for _file in range(2):
            await self.confirm(
                plan(mapping, consentVersion=TERMS_VERSION),
                ("a.txt", WITH_GHOST),
            )
        self.assertEqual(len(await self.rows("campaign_speakers")), 1)

    async def test_another_campaign_sees_none_of_it(self) -> None:
        mapping: dict[str, Any] = {GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        self.assertEqual(await self.rows("campaign_speakers", self.two.id), [])
        self.assertEqual(await self.rows("import_consents", self.two.id), [])

    async def test_a_speaker_with_no_tick_is_refused(self) -> None:
        for bad in ({"confirmed": False}, {}, {"confirmed": "yes"}):
            with self.assertRaises(RecapError) as raised:
                await self.confirm(plan({GHOST: bad}), ("a.txt", WITH_GHOST))
            self.assertEqual(str(raised.exception), "not_ticked")
        self.assertEqual(await self.rows("campaign_speakers"), [])

    async def test_a_stale_wording_is_refused_so_the_page_shows_the_new_one(self) -> None:
        mapping: dict[str, Any] = {GHOST: {"confirmed": True}}
        for version in (None, TERMS_VERSION - 1, str(TERMS_VERSION)):
            with self.assertRaises(RecapError) as raised:
                await self.confirm(plan(mapping, consentVersion=version), ("a.txt", WITH_GHOST))
            self.assertEqual(str(raised.exception), "stale_consent")
        self.assertEqual(await self.lines(), [])

    async def test_an_id_that_is_no_member_of_the_campaign_cannot_be_ticked_in(self) -> None:
        # (a bot, or anyone the member search could never have offered)
        mapping: dict[str, Any] = {"Mira (mira_v)": {"confirmed": True, "member": "888888"}}
        with self.assertRaises(RecapError):
            await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        self.assertEqual(await self.lines(), [])
        self.assertEqual(await self.rows("import_consents"), [])

    async def test_leaving_a_speaker_out_stores_nothing_for_them(self) -> None:
        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"leave_out": True}}
        await self.confirm(plan(mapping), ("a.txt", WITH_GHOST))
        self.assertEqual({user for user, _ in await self.lines()}, {MIRA})
        self.assertEqual(await self.rows("campaign_speakers"), [])

    async def test_the_member_search_greys_out_a_member_who_said_no(self) -> None:
        consent = ConsentStore(self.db)
        await consent.grant(THURSDAY.id, TOMAS)  # a person a character is played by...
        await consent.revoke(THURSDAY.id, TOMAS)  # ...who agreed and then said Stop recording me
        got = await imports.search_members(self.web, self.session, self.ref_c, "", **self.kw)
        by = {m["userId"]: m for m in got["members"]}
        self.assertEqual(by[str(TOMAS)]["blocked"], "said_no")
        self.assertIsNone(by[str(MIRA)]["blocked"])
        self.assertNotIn("refused", by[str(TOMAS)])  # (the page gets the words, not the kind)

    async def test_a_confirmed_speaker_counts_as_recorded_in_that_campaign_only(self) -> None:
        consent = ConsentStore(self.db)
        mapping: dict[str, Any] = {
            "Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)},
            GHOST: {"confirmed": True},
        }
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [speaker] = await self.rows("campaign_speakers")
        ghost = speaker["user_id"]
        here = await consent.recorded_in(THURSDAY.id, self.one.id)
        self.assertIn(TOMAS, here)
        self.assertIn(ghost, here)
        self.assertIn(MIRA, here)  # (and whoever agreed, as before)
        # Another campaign of the same server, and the live allowlist, are untouched.
        elsewhere = await consent.recorded_in(THURSDAY.id, self.two.id)
        self.assertNotIn(TOMAS, elsewhere)
        self.assertNotIn(ghost, elsewhere)
        live = await consent.consenting(THURSDAY.id)
        self.assertEqual(live, frozenset({MIRA}))  # a tick never lets a voice be recorded
        self.assertFalse(consent.has_consent(THURSDAY.id, TOMAS))

    async def test_a_confirmed_member_who_later_says_no_stops_counting(self) -> None:
        consent = ConsentStore(self.db)
        mapping: dict[str, Any] = {"Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        self.assertIn(TOMAS, await consent.recorded_in(THURSDAY.id, self.one.id))
        async with self.db.guild(THURSDAY.id) as conn:  # their later no, newer than the tick
            await conn.execute(
                "INSERT INTO consent_refusals (guild_id, user_id, kind, refused_at)"
                " VALUES (%s, %s, 'stopped', 4000000000)",
                (THURSDAY.id, TOMAS),
            )
        self.assertNotIn(TOMAS, await consent.recorded_in(THURSDAY.id, self.one.id))
        self.assertEqual(len(await self.lines()), 1)  # (their stored line stays; readers stop)

    async def test_the_import_cleaner_uses_the_campaign_rule(self) -> None:
        from dmbot.transcript.import_job import ImportJob, ImportStore

        calls: list[tuple[int, str]] = []

        async def recorded(guild: int, campaign: str) -> frozenset[int]:
            calls.append((guild, campaign))
            return frozenset({TOMAS})

        async def consenting(guild: int) -> frozenset[int]:
            return frozenset()

        async def lookup(guild: int, campaign: str) -> Any:
            return None

        job = ImportJob(ImportStore(self.db), lookup, None, consenting, recorded)
        self.assertEqual(await job._agreed(THURSDAY.id, self.one.id), frozenset({TOMAS}))
        self.assertEqual(calls, [(THURSDAY.id, self.one.id)])
        plain = ImportJob(ImportStore(self.db), lookup, None, consenting)
        self.assertEqual(await plain._agreed(THURSDAY.id, self.one.id), frozenset())

    async def test_a_speaker_of_another_campaign_or_zero_or_junk_cannot_be_ticked(self) -> None:
        # The database says no itself, whatever the page sent (a direct call to the door).
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO campaign_speakers (guild_id, campaign_id, user_id, name, created_by,"
                " created_at) VALUES (%s, %s, -1, 'Other', %s, 1)",
                (THURSDAY.id, self.two.id, ALICE.id),
            )
        line = f'[{{"ms": 0, "user": {TOMAS}, "text": "hello", "dur": 10}}]'
        for ticked in ('["-1"]', '["0"]', '["x"]', "[null]", "[12]", '["99999999999999999999"]'):
            async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
                await _scoped(conn, THURSDAY.id)
                cur = await conn.execute(
                    "SELECT dmbot_import_store(%s, %s, 'x', 'a.txt', 1000, %s::jsonb, %s, NULL,"
                    " %s::jsonb, %s) AS r",
                    (THURSDAY.id, self.one.id, line, TERMS_VERSION, ticked, TERMS_VERSION),
                )
                got = (await cur.fetchone() or {}).get("r")
            self.assertEqual((got or {}).get("error"), "unknown_speaker", ticked)
        self.assertEqual(await self.lines(), [])

    # --- the uploader's own words (#3305) ---
    async def test_a_tick_records_the_uploaders_words_and_a_stale_version_is_refused(self) -> None:
        mapping = {"Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)}}
        with patch.object(uw, "UPLOADER_WORDS_LIVE", True):
            with self.assertRaises(RecapError) as err:  # the Discord version is stale now
                await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
            self.assertEqual(err.exception.code, "stale_consent")
            await self.confirm(
                plan(mapping, consentVersion=uw.IMPORT_WORDS_VERSION), ("a.txt", CRAIG)
            )
        [record] = await self.rows("import_consents")
        self.assertEqual(record["terms_version"], uw.IMPORT_WORDS_VERSION)
        self.assertEqual(record["user_id"], TOMAS)

    async def test_a_member_who_agreed_in_discord_still_counts_under_the_discord_terms(
        self,
    ) -> None:
        # (the import words changing asks nobody's Discord consent again)
        mapping = {"Mira (mira_v)": str(MIRA)}
        with patch.object(uw, "UPLOADER_WORDS_LIVE", True):
            await self.confirm(
                plan(mapping, consentVersion=uw.IMPORT_WORDS_VERSION), ("a.txt", CRAIG)
            )
        self.assertEqual(await self.rows("import_consents"), [])
        self.assertIn(MIRA, {user for user, _ in await self.lines()})

    async def test_stop_recording_me_after_a_tick_removes_them_through_the_real_flow(self) -> None:
        consent = ConsentStore(self.db)
        mapping: dict[str, Any] = {"Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        self.assertIn(TOMAS, await consent.recorded_in(THURSDAY.id, self.one.id))
        await consent.revoke(THURSDAY.id, TOMAS)  # the same call as the Stop button
        self.assertNotIn(TOMAS, await consent.recorded_in(THURSDAY.id, self.one.id))
        await consent.grant(THURSDAY.id, TOMAS)  # and saying yes again clears the no
        self.assertIn(TOMAS, await consent.recorded_in(THURSDAY.id, self.one.id))


class TheBackup(WithImports):
    """The new sections in a backup: a round trip, and a damaged file is refused cleanly."""

    async def rows(self, table: str, campaign: str | None = None) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                f"SELECT * FROM {table} WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, campaign or self.one.id),
            )
            return list(await cur.fetchall())

    async def test_speakers_and_records_survive_a_restore_with_their_lines(self) -> None:
        from dmbot.bot import register_backup_sections
        from dmbot.campaigns import CampaignStore

        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        store = CampaignStore(self.db)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=ConsentStore(self.db).consenting,
        )
        data, parts = await store.export_set(THURSDAY.id, self.one.id)
        copy = await store.import_backup(THURSDAY.id, data, ALICE.id, parts=parts)
        [speaker] = await self.rows("campaign_speakers", copy.id)
        [record] = await self.rows("import_consents", copy.id)
        [original] = await self.rows("campaign_speakers")
        self.assertEqual(speaker["user_id"], original["user_id"])  # the same number, no renumbering
        self.assertEqual(record["user_id"], speaker["user_id"])

    def test_a_damaged_speaker_or_record_is_refused(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.transcript.import_consent import ImportConsentsSection, SpeakersSection

        good = {"id": "-3", "name": "Old Greg", "by": "7", "at": 1}
        self.assertEqual(len(SpeakersSection().check([good])), 1)
        for bad in ({"id": "-99999999999999999999"}, {"id": "3"}, {"id": "0"}, {"name": ""}):
            with self.assertRaises(CampaignError, msg=str(bad)):
                SpeakersSection().check([{**good, **bad}])
        with self.assertRaises(CampaignError):
            ImportConsentsSection().check([{"id": "x"}])


NOBODY_ID = 7003
