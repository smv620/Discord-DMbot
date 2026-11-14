"""A DM removes one speaker's lines from one campaign (#3305, PR 2): every trace goes in that
campaign and none in another, the removal holds after a restore of an older copy, a removed
speaker's number is not given again, and the website's door is the DM's alone. Invented fixtures
only."""

from __future__ import annotations

from typing import Any, cast

from dmbot.consent import TERMS_VERSION, ConsentStore
from dmbot.transcript.models import Line
from dmbot.web import imports
from dmbot.web import sessions as web_sessions
from dmbot.web.recaps import RecapError
from tests.test_import_consent import GHOST, WITH_GHOST, plan
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_imports import CRAIG, MIRA, TOMAS, WithImports

OTHER_START = 1_700_000_000


class Removing(WithImports):
    async def rows(self, table: str, campaign: str | None = None) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                f"SELECT * FROM {table} WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, campaign or self.one.id),
            )
            return list(await cur.fetchall())

    async def lines_of(self, campaign: str, user: int) -> int:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_lines l JOIN transcript_sessions s"
                " ON s.id = l.session_id AND s.guild_id = l.guild_id"
                " WHERE l.guild_id = %s AND s.campaign_id = %s AND l.user_id = %s",
                (THURSDAY.id, campaign, user),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def give(self, campaign: str, user: int, *words: str) -> str:
        """Lines of `user` in a new session of `campaign`, as a recording would save them."""
        sid = await self.transcripts.open_session(THURSDAY.id, campaign, OTHER_START + len(words))
        await self.transcripts.add_lines(
            THURSDAY.id,
            sid,
            [Line((OTHER_START + i) * 1000, user, w, w) for i, w in enumerate(words)],
        )
        return sid

    async def ghost(self) -> int:
        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [speaker] = await self.rows("campaign_speakers")
        return int(speaker["user_id"])

    async def test_a_speaker_without_discord_goes_from_that_campaign_only(self) -> None:
        number = await self.ghost()
        await self.give(self.two.id, number, "someone else's -1")  # the same number, elsewhere
        gone = await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, number)
        self.assertEqual((gone.lines, gone.sessions), (1, 1))
        self.assertEqual(await self.lines_of(self.one.id, number), 0)
        self.assertEqual(await self.lines_of(self.two.id, number), 1)  # another campaign's
        self.assertEqual(await self.rows("campaign_speakers"), [])  # the name is gone too
        [mark] = await self.rows("speaker_erasures")
        self.assertEqual(mark["user_id"], number)
        [record] = await self.rows("import_consents")
        self.assertEqual(record["lines"], 0)  # the record stays, with when it was removed
        self.assertIsNotNone(record["removed_at"])
        [session] = [s for s in await self.rows("transcript_sessions")]
        self.assertNotIn(str(number), session["speaker_names"])
        self.assertNotIn(number, session["speakers"])

    async def test_a_member_goes_from_that_campaign_only(self) -> None:
        await ConsentStore(self.db).grant(THURSDAY.id, MIRA)
        await self.give(self.one.id, MIRA, "in one")
        await self.give(self.two.id, MIRA, "in two")
        async with self.db.guild(THURSDAY.id) as conn:  # a label an import mapped to her
            await conn.execute(
                "INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)"
                " VALUES (%s, %s, 'Mira (mira_v)', %s, 1)",
                (THURSDAY.id, self.one.id, MIRA),
            )
        gone = await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, MIRA)
        self.assertEqual(gone.lines, 1)
        self.assertEqual(await self.lines_of(self.one.id, MIRA), 0)
        self.assertEqual(await self.lines_of(self.two.id, MIRA), 1)
        self.assertEqual(await self.rows("import_speakers"), [])
        self.assertEqual([r["user_id"] for r in await self.rows("speaker_erasures")], [MIRA])
        self.assertEqual(await self.rows("speaker_erasures", self.two.id), [])

    async def test_nothing_left_still_writes_the_marker_and_reports_zero(self) -> None:
        gone = await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, MIRA)
        self.assertEqual((gone.lines, gone.sessions), (0, 0))
        self.assertEqual(len(await self.rows("speaker_erasures")), 1)

    async def test_a_removed_speakers_number_is_not_given_again(self) -> None:
        number = await self.ghost()
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, number)
        mapping: dict[str, Any] = {GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        [again] = await self.rows("campaign_speakers")
        self.assertLess(again["user_id"], number)  # a new number, not the removed one's
        self.assertEqual(await self.lines_of(self.one.id, number), 0)

    async def test_an_older_copy_of_the_same_campaign_does_not_bring_the_lines_back(self) -> None:
        from dmbot.bot import register_backup_sections
        from dmbot.campaigns import CampaignStore

        number = await self.ghost()
        await self.give(self.one.id, MIRA, "mira keeps her words")
        store = CampaignStore(self.db)
        register_backup_sections(
            store, forget_audio=lambda g, c: None, consenting=ConsentStore(self.db).consenting
        )
        data, parts = await store.export_set(THURSDAY.id, self.one.id)  # made BEFORE the removal
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, number)
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, MIRA)
        await store.import_backup(
            THURSDAY.id, data, ALICE.id, parts=parts, replace_campaign_id=self.one.id
        )
        self.assertEqual(await self.lines_of(self.one.id, number), 0)
        self.assertEqual(await self.lines_of(self.one.id, MIRA), 0)
        self.assertEqual(await self.rows("campaign_speakers"), [])  # not named again either
        marks = {r["user_id"] for r in await self.rows("speaker_erasures")}
        self.assertEqual(marks, {number, MIRA})  # a restore never forgets a removal

    async def test_a_copy_made_after_the_removal_carries_it_into_a_new_campaign(self) -> None:
        from dmbot.bot import register_backup_sections
        from dmbot.campaigns import CampaignStore

        number = await self.ghost()
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, number)
        store = CampaignStore(self.db)
        register_backup_sections(
            store, forget_audio=lambda g, c: None, consenting=ConsentStore(self.db).consenting
        )
        data, parts = await store.export_set(THURSDAY.id, self.one.id)
        copy = await store.import_backup(THURSDAY.id, data, ALICE.id, parts=parts)
        self.assertEqual(
            [r["user_id"] for r in await self.rows("speaker_erasures", copy.id)], [number]
        )

    async def test_a_removed_speakers_consent_no_longer_counts(self) -> None:
        number = await self.ghost()
        consent = ConsentStore(self.db)
        self.assertIn(number, await consent.recorded_in(THURSDAY.id, self.one.id))
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, number)
        self.assertNotIn(number, await consent.recorded_in(THURSDAY.id, self.one.id))

    async def test_the_door_is_the_dms_and_takes_counts_only(self) -> None:
        number = await self.ghost()
        token = await web_sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await web_sessions.find(self.web, token, now=NOW)
        assert bob is not None  # not a DM of this campaign
        with self.assertRaises(RecapError):
            await imports.remove_speaker(self.web, bob, self.ref_c, str(number), **self.kw)
        asked = await imports.remove_speaker(
            self.web, self.session, self.ref_c, str(number), **self.kw
        )
        self.assertEqual(asked["status"], "queued")
        again = await imports.remove_speaker(
            self.web, self.session, self.ref_c, str(number), **self.kw
        )
        self.assertEqual(again["id"], asked["id"])  # a second press is the same request
        with self.assertRaises(RecapError):  # a number that is nobody's here queues nothing
            await imports.remove_speaker(self.web, self.session, self.ref_c, "-999", **self.kw)
        [row] = await self.rows("speaker_removals")
        self.assertEqual(
            (row["state"], row["stage"], row["speaker"]), ("waiting", "waiting", number)
        )
        self.assertEqual({"lines", "sessions"} <= set(row), True)  # counts, never words

    async def test_the_list_also_has_a_member_the_uploader_ticked_in(self) -> None:
        mapping: dict[str, Any] = {
            "Mira (mira_v)": str(MIRA),  # agreed in Discord: no record, not listed
            "Tomas (tomas#1)": {"confirmed": True, "member": str(TOMAS)},  # ticked in
        }
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", CRAIG))
        got = await imports.speakers(self.web, self.session, self.ref_c, **self.kw)
        [one] = got["speakers"]
        self.assertEqual(one, {"id": str(TOMAS), "name": "Player 1", "member": True, "lines": 1})
        await self.transcripts.remove_speaker(THURSDAY.id, self.one.id, TOMAS)
        again = await imports.speakers(self.web, self.session, self.ref_c, **self.kw)
        self.assertEqual(again["speakers"], [])  # removed: no longer listed
