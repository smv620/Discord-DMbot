"""The recap texts a DM approved, kept as each session's summary (#2122): a new version only for
new text, never overwritten, per campaign and server, in the backup, and gone with the campaign.
Run through the website's approval door, as the DM does."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.consent import TERMS_VERSION
from dmbot.recaps.accepted import VERSIONS_PER_SESSION, AcceptedSection, AcceptedStore
from dmbot.recaps.approval import text_hash
from dmbot.web.recaps import approve_recap
from tests import recap_fixtures as fx
from tests.test_recap_web import NOW, Base
from tests.test_web_api import ALICE, THURSDAY

TEXT = "Mira drew her blade. 《Calm》 The door opened."


class Accepting(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.accepted = AcceptedStore(self.db)
        async with self.db.guild(THURSDAY.id) as conn:  # the speaker is recorded
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, %s, %s)",
                (THURSDAY.id, fx.ALICE, NOW, TERMS_VERSION),
            )

    async def approve(self, ref: str | None = None) -> None:
        await approve_recap(self.web, self.session, ref or self.ref, "text", audio_in_plan=False)

    async def edit(self, summary: str, campaign: str | None = None) -> None:
        await self.store.update(
            THURSDAY.id, campaign or self.one.id, 100, summary=summary, state="ready"
        )

    async def test_the_approved_text_becomes_version_one(self) -> None:
        await self.approve()
        [a] = await self.accepted.versions(THURSDAY.id, self.one.id, 100)
        job = await self.state(self.one.id)
        self.assertEqual((a.version, a.summary, a.title), (1, job.summary, job.title))
        self.assertEqual(a.text_hash, text_hash(job.title, job.summary))
        self.assertEqual(a.accepted_by, ALICE.id)  # who, and when (the database's own clock)
        self.assertGreater(a.accepted_at, 0)

    async def test_approving_the_same_text_again_adds_nothing(self) -> None:
        await self.approve()
        await self.approve()
        self.assertEqual(len(await self.accepted.versions(THURSDAY.id, self.one.id, 100)), 1)

    async def test_a_changed_text_is_the_next_version_and_the_old_one_stays(self) -> None:
        await self.approve()
        first = (await self.accepted.versions(THURSDAY.id, self.one.id, 100))[0]
        await self.edit(TEXT)
        await self.approve()
        versions = await self.accepted.versions(THURSDAY.id, self.one.id, 100)
        self.assertEqual([v.version for v in versions], [1, 2])
        self.assertEqual(versions[0], first)  # never overwritten
        self.assertEqual(versions[1].summary, TEXT)
        [newest] = await self.accepted.newest(THURSDAY.id, self.one.id)
        self.assertEqual(newest.version, 2)

    async def test_going_back_to_an_earlier_text_is_a_new_version(self) -> None:
        await self.approve()
        original = (await self.state(self.one.id)).summary
        await self.edit(TEXT)
        await self.approve()
        await self.edit(original)
        await self.approve()
        versions = await self.accepted.versions(THURSDAY.id, self.one.id, 100)
        self.assertEqual([v.version for v in versions], [1, 2, 3])
        self.assertEqual(versions[2].text_hash, versions[0].text_hash)

    async def test_the_newest_of_each_session_in_order(self) -> None:
        await self.approve()
        await self.edit(TEXT)
        await self.approve()
        async with self.db.guild(THURSDAY.id) as conn:  # an earlier session, approved once
            await conn.execute(
                "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                " text_hash, title, summary, accepted_by, accepted_at)"
                " VALUES (%s, %s, 50, 1, %s, 'Before', 'Earlier.', %s, 1)",
                (THURSDAY.id, self.one.id, "a" * 64, ALICE.id),
            )
        newest = await self.accepted.newest(THURSDAY.id, self.one.id)
        self.assertEqual([(a.session_at, a.version) for a in newest], [(50, 1), (100, 2)])

    async def test_each_campaign_keeps_its_own(self) -> None:
        await self.approve()
        self.assertEqual(await self.accepted.newest(THURSDAY.id, self.two.id), [])
        self.assertEqual(await self.accepted.versions(THURSDAY.id, self.two.id, 100), [])

    async def test_another_server_sees_none_of_it(self) -> None:
        await self.approve()
        self.assertEqual(await self.accepted.newest(THURSDAY.id + 1, self.one.id), [])

    async def test_it_goes_in_the_backup_and_comes_back_whole(self) -> None:
        await self.approve()
        await self.edit(TEXT)
        await self.approve()
        section = AcceptedSection()
        async with self.db.guild(THURSDAY.id) as conn:
            dumped = await section.dump(conn, THURSDAY.id, self.one.id)
            self.assertEqual([r["version"] for r in dumped], [1, 2])
            self.assertIsInstance(dumped[0]["accepted_by"], str)  # ids travel as text
            await section.clear(conn, THURSDAY.id, self.one.id)
            self.assertEqual(await section.dump(conn, THURSDAY.id, self.one.id), [])
            await section.load(conn, THURSDAY.id, self.one.id, dumped)
            self.assertEqual(await section.dump(conn, THURSDAY.id, self.one.id), dumped)

    async def test_it_goes_with_the_campaign(self) -> None:
        await self.approve()
        await self.campaigns.delete(THURSDAY.id, self.one.id)
        self.assertEqual(await self.accepted.newest(THURSDAY.id, self.one.id), [])

    async def test_the_website_can_neither_read_nor_write_the_table_itself(self) -> None:
        import psycopg.errors as pg_errors

        for sql in (
            "INSERT INTO recap_accepted VALUES (%s, %s, 1, 1, 'x', 'x', 'x', 1, 1)",
            "SELECT * FROM recap_accepted WHERE guild_id = %s AND campaign_id = %s",
        ):
            async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
                with self.assertRaises(pg_errors.InsufficientPrivilege):
                    await conn.execute(sql, (THURSDAY.id, self.one.id))

    async def test_the_door_refuses_a_stranger_and_a_stale_text_and_writes_no_version(self) -> None:
        from dmbot.web.recaps import RecapError

        with self.assertRaises(RecapError):  # Alice asks about Bob's recap
            await self.approve(self.other)
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_recap_approve(%s, %s, 100, 'text', %s) AS r",
                (THURSDAY.id, self.one.id, "0" * 64),  # the text changed since the page read it
            )
            self.assertEqual((await cur.fetchone() or {}).get("r"), "not_allowed")
        self.assertEqual(await self.accepted.newest(THURSDAY.id, self.one.id), [])
        self.assertEqual(await self.accepted.newest(THURSDAY.id, self.two.id), [])

    async def test_a_session_keeps_the_first_and_the_nineteen_newest(self) -> None:
        for n in range(25):
            await self.edit(f"Version {n}.")
            await self.approve()
        versions = await self.accepted.versions(THURSDAY.id, self.one.id, 100)
        self.assertEqual(len(versions), VERSIONS_PER_SESSION)
        # the first is never lost, nothing stored is rewritten, numbers only go up
        self.assertEqual([v.version for v in versions], [1, *range(7, 26)])
        self.assertEqual([v.summary for v in versions][:2], ["Version 0.", "Version 6."])
        self.assertEqual(versions[-1].summary, "Version 24.")
        async with self.db.guild(THURSDAY.id) as conn:  # and the backup of it restores
            dumped = await AcceptedSection().dump(conn, THURSDAY.id, self.one.id)
        AcceptedSection().check(dumped)

    async def test_the_discord_button_keeps_versions_the_same_way(self) -> None:
        job = await self.state(self.one.id)
        digest = text_hash(job.title, job.summary)
        ok = await self.store.approve(
            THURSDAY.id, self.one.id, 100, ALICE.id, "text", expected=digest
        )
        self.assertTrue(ok)
        [a] = await self.accepted.versions(THURSDAY.id, self.one.id, 100)
        self.assertEqual((a.version, a.text_hash, a.accepted_by), (1, digest, ALICE.id))
        stale = await self.store.approve(
            THURSDAY.id, self.one.id, 100, ALICE.id, "text", expected="0" * 64
        )
        self.assertFalse(stale)  # a refused approval keeps nothing
        self.assertEqual(len(await self.accepted.versions(THURSDAY.id, self.one.id, 100)), 1)

    async def test_it_restores_through_the_campaign_backup(self) -> None:
        self.campaigns.register_section(AcceptedSection())
        await self.approve()
        await self.edit(TEXT)
        await self.approve()
        data = await self.campaigns.export(THURSDAY.id, self.one.id)
        restored = await self.campaigns.import_backup(THURSDAY.id, data, ALICE.id)
        versions = await self.accepted.versions(THURSDAY.id, restored.id, 100)
        self.assertEqual([v.version for v in versions], [1, 2])
        data["sections"]["recap_accepted"][0]["summary"] = "tampered"  # the hash no longer fits
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(THURSDAY.id, data, ALICE.id)


class Checking(unittest.TestCase):
    def good(self) -> dict[str, Any]:
        return {
            "session_at": 100,
            "version": 1,
            "text_hash": text_hash("The Silver Lock", "Mira drew her blade."),
            "title": "The Silver Lock",
            "summary": "Mira drew her blade.",
            "accepted_by": "7",
            "accepted_at": 5,
        }

    def test_more_than_twenty_versions_of_one_session_are_refused(self) -> None:
        rows = [{**self.good(), "version": n} for n in range(1, 22)]
        with self.assertRaises(CampaignError):
            AcceptedSection().check(rows)
        AcceptedSection().check(rows[:20])

    def test_a_good_row_is_read(self) -> None:
        [a] = AcceptedSection().check([self.good()])
        self.assertEqual((a.session_at, a.version, a.accepted_by), (100, 1, 7))

    def test_every_damaged_row_is_refused_in_plain_words(self) -> None:
        good = self.good()
        damaged: list[Any] = [
            "x",
            {**good, "extra": 1},
            {**good, "session_at": 0},
            {**good, "session_at": "100"},
            {**good, "version": 0},
            {**good, "version": True},
            {**good, "text_hash": "short"},
            {**good, "text_hash": "b" * 64},  # not the text's own hash
            {**good, "version": 2**31},  # past what the column holds
            {**good, "title": ""},
            {**good, "summary": "x" * 6001},
            {**good, "accepted_by": "-1"},
            {**good, "accepted_by": 7},
            {**good, "accepted_at": -1},
        ]
        for row in damaged:
            with self.assertRaises(CampaignError, msg=str(row)[:60]):
                AcceptedSection().check([row])
        with self.assertRaises(CampaignError):  # the same session and version twice
            AcceptedSection().check([good, dict(good)])
        with self.assertRaises(CampaignError):
            AcceptedSection().check([{**good, "session_at": n + 1} for n in range(5001)])
