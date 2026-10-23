"""The recap texts a DM approved, kept as each session's summary (#2122): a new version only for
new text, never overwritten, per campaign and server, in the backup, and gone with the campaign.
Run through the website's approval door, as the DM does."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.consent import TERMS_VERSION
from dmbot.recaps.accepted import AcceptedSection, AcceptedStore
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

    async def test_the_website_cannot_write_a_version_itself(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (the role has no right to the table)
                await conn.execute(
                    "INSERT INTO recap_accepted VALUES (%s, %s, 1, 1, 'x', 'x', 'x', 1, 1)",
                    (THURSDAY.id, self.one.id),
                )


class Checking(unittest.TestCase):
    def good(self) -> dict[str, Any]:
        return {
            "session_at": 100,
            "version": 1,
            "text_hash": "a" * 64,
            "title": "The Silver Lock",
            "summary": "Mira drew her blade.",
            "accepted_by": "7",
            "accepted_at": 5,
        }

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
            AcceptedSection().check([good] * 401)
