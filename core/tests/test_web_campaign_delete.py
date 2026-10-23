"""The owner's own campaigns in any server, and deleting one from the website (#2554, part 1): the
owner sees a campaign in a server the session's list does not have, a game master who is not the
owner does not, and the delete follows Discord's rule (the owner; with no owner any game master who
can reach the server; the typed name; not during a session)."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from dmbot.campaigns.web_delete import KEEP_UNTAKEN_S, Notice, WebDeleteNotices
from dmbot.recaps.audio.files import AudioFiles
from dmbot.web import campaign_delete, sessions
from dmbot.web.app import create_app
from dmbot.web.me import build_me
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

LOST = 777  # a server Alice's session does not have (left, removed, gone)


class Deleting(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lost = await self.campaigns.create(LOST, "Lost World", ALICE.id)
        self.audio_root = Path(tempfile.mkdtemp())
        self.audio = AudioFiles(self.audio_root)

    async def signed(self, user: Any, *guilds: Any) -> Any:
        token = await sessions.sign_in(self.web, user, list(guilds), now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def cards(self, session: Any) -> dict[str, dict[str, Any]]:
        me = await build_me(self.web, session, now=NOW)
        return {c["name"]: c for c in me["campaigns"]}

    async def delete(self, session: Any, campaign: Any, guild: int, name: str) -> dict[str, Any]:
        return await campaign_delete.delete(
            self.web, session, f"{guild}.{campaign.id}", {"name": name}, audio=None
        )

    async def refused(self, session: Any, campaign: Any, guild: int, name: str) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.delete(session, campaign, guild, name)
        return caught.exception.code

    async def sql(self, guild: int, statement: str, *args: Any) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(statement, args)

    # ---- the list ----------------------------------------------------------

    async def test_the_owner_sees_a_campaign_in_a_server_the_session_does_not_have(self) -> None:
        cards = await self.cards(self.session)
        lost = cards["Lost World"]
        self.assertEqual(lost["ref"], f"{LOST}.{self.lost.id}")
        self.assertEqual(
            (lost["role"], lost["serverReachable"], lost["canDelete"], lost["serverName"]),
            ("owner", False, True, ""),
        )
        here = cards["One"]
        self.assertEqual((here["serverReachable"], here["canDelete"]), (True, True))

    async def test_a_server_dmbot_has_left_is_not_reachable(
        self,
    ) -> None:
        async with self.db.user(ALICE.id, install_guild=THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via,"
                " left_at) VALUES (%s, %s, 1, 'site', 2)",
                (THURSDAY.id, ALICE.id),
            )
        cards = await self.cards(self.session)
        self.assertEqual(
            (cards["One"]["serverReachable"], cards["One"]["canDelete"]), (False, True)
        )

    async def test_a_game_master_who_is_not_the_owner_does_not_see_it(self) -> None:
        await self.campaigns.add_dm(LOST, self.lost.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        self.assertNotIn("Lost World", await self.cards(bob))

    async def test_a_co_dm_card_cannot_be_deleted_but_a_no_owner_one_can(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        self.assertFalse((await self.cards(bob))["One"]["canDelete"])
        await self.sql(
            THURSDAY.id, "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", self.one.id
        )
        self.assertTrue((await self.cards(bob))["One"]["canDelete"])

    # ---- delete ------------------------------------------------------------

    async def test_the_owner_deletes_a_campaign_in_an_unreachable_server_and_nothing_else_changes(
        self,
    ) -> None:
        folder = self.audio_root / str(LOST) / self.lost.id / "recaps/100"
        folder.mkdir(parents=True)
        (folder / "recap-voice.mp3").write_bytes(b"x")
        answer = await self.delete(self.session, self.lost, LOST, "lost world")
        self.assertEqual(answer, {"status": "deleted", "channelsLeft": False})
        self.assertIsNone(await self.campaigns.get(LOST, self.lost.id))
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.one.id))
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.two.id))
        self.assertNotIn("Lost World", await self.cards(self.session))
        # The web container cannot remove audio (its volume is read-only): the bot's sweep does.
        self.assertTrue(folder.exists())
        self.assertEqual(await self.sweep(), 1)
        self.assertFalse((self.audio_root / str(LOST) / self.lost.id).exists())

    async def sweep(self, me: Any = None, passes: int = 2) -> int:
        """The bot's own method, on a stand-in with the real store and the real audio files. A
        folder goes only when two passes in a row list it, so a test runs two."""
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        me = me or SimpleNamespace(campaigns=self.campaigns, recap_audio_files=self.audio)
        before = sum(1 for _ in self.audio_root.glob("*/*"))
        for _ in range(passes):
            await DMBot._sweep_deleted_campaign_audio(me)
        return before - sum(1 for _ in self.audio_root.glob("*/*"))

    async def test_one_pass_removes_nothing_the_next_one_does(self) -> None:
        stray = self.audio_root / str(LOST) / "gonecampaign" / "recaps/1"
        stray.mkdir(parents=True)
        self.assertEqual(await self.sweep(passes=1), 0)
        self.assertTrue(stray.exists())

    async def test_a_move_that_lands_between_the_reads_of_one_pass_loses_nothing(self) -> None:
        """A pass reads the new server first (the campaign is not there yet), then the move
        commits, then it reads the old server (the campaign is gone from it): in that pass the
        campaign looks deleted and its folder in the new server a stray. It is not removed on that
        sighting, and the next pass, reading a settled database, finds the campaign there."""
        from types import SimpleNamespace

        moved = self.audio_root / "999" / self.one.id / "recaps/1"
        moved.mkdir(parents=True)
        one, two = self.one.id, self.two.id

        class Flaky:
            settled = False

            async def guilds_with_campaigns(self_) -> list[int]:
                return [999, THURSDAY.id]

            async def campaign_ids(self_, guild_id: int) -> set[str]:
                if guild_id == 999:
                    return {one} if self_.settled else set()
                return {two}

            async def exists_anywhere(self_, campaign_id: str) -> bool:
                return campaign_id == one and self_.settled

        store = Flaky()
        me = SimpleNamespace(campaigns=store, recap_audio_files=self.audio)
        self.assertEqual(await self.sweep(me, passes=1), 0)  # looks gone, first sighting
        store.settled = True
        self.assertEqual(await self.sweep(me, passes=1), 0)  # the next pass finds it
        self.assertTrue(moved.exists())

    async def test_even_a_second_sighting_does_not_remove_a_campaign_found_again(self) -> None:
        from types import SimpleNamespace

        moved = self.audio_root / "999" / self.one.id / "recaps/1"
        moved.mkdir(parents=True)
        one, two = self.one.id, self.two.id

        class Unsettled:
            async def guilds_with_campaigns(self_) -> list[int]:
                return [999, THURSDAY.id]

            async def campaign_ids(self_, guild_id: int) -> set[str]:
                return set() if guild_id == 999 else {two}

            async def exists_anywhere(self_, campaign_id: str) -> bool:
                return campaign_id == one  # the fresh, single lookup finds it

        me = SimpleNamespace(campaigns=Unsettled(), recap_audio_files=self.audio)
        self.assertEqual(await self.sweep(me), 0)
        self.assertTrue(moved.exists())

    async def test_the_sweep_removes_only_the_folders_of_campaigns_that_exist_nowhere(self) -> None:
        mine = self.audio_root / str(THURSDAY.id) / self.one.id / "recaps/1"
        mine.mkdir(parents=True)
        stray = self.audio_root / str(LOST) / "gonecampaign" / "recaps/1"
        stray.mkdir(parents=True)
        self.assertEqual(await self.sweep(), 1)
        self.assertTrue(mine.exists())
        self.assertFalse(stray.parent.parent.exists())

    async def test_a_campaign_that_moved_servers_keeps_its_new_folder(self) -> None:
        """The folder in the new server's directory is not in an older snapshot, and it is as old
        as the files copied into it: the sweep looks the campaign up again and leaves it."""
        moved = self.audio_root / "999" / self.one.id / "recaps/1"
        moved.mkdir(parents=True)
        self.assertEqual(await self.sweep(), 0)
        self.assertTrue(moved.exists())

    async def test_a_copy_in_progress_and_a_symlink_are_never_removed(self) -> None:
        copying = self.audio_root / str(LOST) / "midcopy"
        (copying / "recaps").mkdir(parents=True)
        (copying / "recaps" / "1.copying").write_bytes(b"x")
        outside = Path(tempfile.mkdtemp())
        link = self.audio_root / str(LOST) / "linked"
        link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(await self.sweep(), 0)
        self.assertTrue(copying.exists())
        self.assertTrue(link.is_symlink())

    async def test_the_sweep_does_nothing_when_no_server_lists_a_campaign(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        folder = self.audio_root / str(LOST) / "somecampaign"
        folder.mkdir(parents=True)

        class Empty:
            async def guilds_with_campaigns(self) -> list[int]:
                return []

        me = SimpleNamespace(campaigns=Empty(), recap_audio_files=self.audio)
        await DMBot._sweep_deleted_campaign_audio(me)  # type: ignore[arg-type]
        self.assertTrue(folder.exists())

    async def test_the_typed_name_matches_the_way_discords_delete_does(self) -> None:
        odd = await self.campaigns.create(THURSDAY.id, "Straße  Watch", ALICE.id)
        self.assertEqual(
            await self.refused(self.session, odd, THURSDAY.id, "Strasse"), "name_mismatch"
        )
        answer = await self.delete(self.session, odd, THURSDAY.id, "  STRASSE watch ")
        self.assertEqual(answer["status"], "deleted")

    async def test_the_answer_says_when_discords_channels_are_left(self) -> None:
        await self.sql(
            LOST, "UPDATE campaigns SET dm_screen_channel_id = 5 WHERE id = %s", self.lost.id
        )
        answer = await self.delete(self.session, self.lost, LOST, "Lost World")
        self.assertTrue(answer["channelsLeft"])

    async def test_a_game_master_who_is_not_the_owner_is_refused_in_a_server_they_can_reach(
        self,
    ) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.refused(bob, self.one, THURSDAY.id, "One"), "not_the_owner")
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.one.id))

    async def test_a_game_master_cannot_reach_a_campaign_in_a_server_outside_their_session(
        self,
    ) -> None:
        await self.campaigns.add_dm(LOST, self.lost.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.refused(bob, self.lost, LOST, "Lost World"), "not_found")
        self.assertIsNotNone(await self.campaigns.get(LOST, self.lost.id))

    async def test_another_accounts_campaign_is_not_found(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.refused(bob, self.one, THURSDAY.id, "One"), "not_found")
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.one.id))

    async def test_a_wrong_name_deletes_nothing(self) -> None:
        self.assertEqual(
            await self.refused(self.session, self.one, THURSDAY.id, "Two"), "name_mismatch"
        )
        self.assertEqual(
            await self.refused(self.session, self.one, THURSDAY.id, ""), "name_mismatch"
        )
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.one.id))

    async def test_a_running_session_stops_it(self) -> None:
        await self.sql(
            THURSDAY.id,
            "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
            " screen_channel_id, started_at, started_by) VALUES (%s, %s, 1, 2, 3, %s)",
            THURSDAY.id, self.one.id, ALICE.id,
        )  # fmt: skip
        self.assertEqual(
            await self.refused(self.session, self.one, THURSDAY.id, "One"), "session_running"
        )
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.one.id))

    async def test_with_no_owner_any_game_master_who_can_reach_the_server_may_delete(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        await self.sql(
            THURSDAY.id, "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", self.one.id
        )
        bob = await self.signed(BOB, THURSDAY)
        answer = await self.delete(bob, self.one, THURSDAY.id, "One")
        self.assertEqual(answer["status"], "deleted")
        self.assertIsNone(await self.campaigns.get(THURSDAY.id, self.one.id))
        self.assertIsNotNone(await self.campaigns.get(THURSDAY.id, self.two.id))

    async def test_with_no_owner_someone_who_is_not_a_game_master_is_not_found(self) -> None:
        await self.sql(
            THURSDAY.id, "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", self.one.id
        )
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        self.assertEqual(await self.refused(bob, self.one, THURSDAY.id, "One"), "not_found")

    async def test_a_bad_address_or_body_is_not_found_or_a_wrong_name(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await campaign_delete.delete(
                self.web, self.session, "nonsense", {"name": "x"}, audio=None
            )
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as caught:
            await campaign_delete.delete(
                self.web, self.session, f"{THURSDAY.id}.{self.one.id}", "x", audio=None
            )
        self.assertEqual(caught.exception.code, "name_mismatch")


class TheRoute(Deleting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)

    async def test_the_answers_over_http(self) -> None:
        url = f"/me/campaigns/{THURSDAY.id}.{self.one.id}/delete"
        wrong = await self.client.post(url, json={"name": "Two"}, headers=HEADERS)
        self.assertEqual((wrong.status_code, wrong.json()["error"]), (422, "name_mismatch"))
        elsewhere = await self.client.post(
            f"/me/campaigns/{THURSDAY.id}.{self.two.id}/delete",
            json={"name": "Two"},
            headers=HEADERS,
        )
        self.assertEqual((elsewhere.status_code, elsewhere.json()["error"]), (404, "not_found"))
        done = await self.client.post(url, json={"name": "One"}, headers=HEADERS)
        self.assertEqual(
            (done.status_code, done.json()), (200, {"status": "deleted", "channelsLeft": False})
        )
        lost = await self.client.post(
            f"/me/campaigns/{LOST}.{self.lost.id}/delete",
            json={"name": "Lost World"},
            headers=HEADERS,
        )
        self.assertEqual(lost.status_code, 200)  # a server the session does not have
        me = (await self.client.get("/me")).json()
        self.assertEqual([c["name"] for c in me["campaigns"]], [])


class TheDeleteNotice(Deleting):
    """What the website's delete leaves for the bot (#2554, part 1b)."""

    async def test_the_delete_leaves_one_note_with_the_channels_and_the_other_game_masters(
        self,
    ) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        await self.sql(
            THURSDAY.id,
            "UPDATE campaigns SET dm_screen_channel_id = 11, transcript_channel_id = 22,"
            " npcs_channel_id = 33 WHERE id = %s",
            self.one.id,
        )
        await self.delete(self.session, self.one, THURSDAY.id, "one")
        notes = WebDeleteNotices(self.db)
        got = await notes.take(THURSDAY.id)
        self.assertEqual(len(got), 1)
        note = got[0]
        self.assertEqual(
            (note.id, note.name, note.by_user_id, note.other_dms),
            (self.one.id, "One", ALICE.id, (BOB.id,)),
        )
        self.assertEqual(
            (note.dm_screen_channel_id, note.transcript_channel_id, note.npcs_channel_id),
            (11, 22, 33),
        )
        self.assertIsNone(note.gametime_channel_id)
        self.assertEqual(await notes.take(THURSDAY.id), [])  # taken once

    async def test_a_refused_delete_leaves_no_note(self) -> None:
        await self.refused(self.session, self.one, THURSDAY.id, "wrong name")
        self.assertEqual(await WebDeleteNotices(self.db).take(THURSDAY.id), [])

    async def test_a_note_is_only_seen_in_its_own_server(self) -> None:
        await self.delete(self.session, self.lost, LOST, "lost world")
        notes = WebDeleteNotices(self.db)
        self.assertEqual(await notes.take(THURSDAY.id), [])
        self.assertEqual(len(await notes.take(LOST)), 1)

    async def test_an_untaken_note_is_dropped_after_a_week(self) -> None:
        await self.delete(self.session, self.one, THURSDAY.id, "one")
        later = WebDeleteNotices(self.db, clock=lambda: time.time() + KEEP_UNTAKEN_S + 60)
        self.assertEqual(await later.sweep(THURSDAY.id), 1)
        self.assertEqual(await WebDeleteNotices(self.db).take(THURSDAY.id), [])

    async def test_the_bot_tidies_and_tells_the_other_game_masters(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        told: list[tuple[int, str]] = []
        tidied: list[Notice] = []

        async def dm_user(user_id: int, text: str) -> bool:
            if user_id == 3:
                raise RuntimeError("messages are off")  # one failing must not stop the rest
            told.append((user_id, text))
            return True

        async def retire(note: Notice, now: int, *, by_hand: bool = False) -> None:
            tidied.append(note)

        note = Notice(THURSDAY.id, "c1", "Frost*maiden", 1001, (2, 3, 4), 11, None, None, None,
                      None, None, None)  # fmt: skip
        me = SimpleNamespace(
            _retire_campaign_channels=retire, _dm_user=dm_user, name_of=lambda g, u: "Ali_ce"
        )
        await DMBot._finish_web_delete(me, note)  # type: ignore[arg-type]
        self.assertEqual(tidied, [note])
        self.assertEqual([u for u, _ in told], [2, 4])
        self.assertIn("deleted **Frost\\*maiden**", told[0][1])
        self.assertIn("Ali\\_ce", told[0][1])

    async def test_a_server_the_bot_has_left_is_still_finished(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        taken: list[int] = []
        finished: list[Notice] = []
        forgotten: list[int] = []
        note = Notice(555, "c1", "Gone", 1, (2,), 11, None, None, None, None, None, None)

        class Notes:
            async def take(self, guild_id: int) -> list[Notice]:
                taken.append(guild_id)
                return [note] if guild_id == 555 else []

        class Campaigns:
            async def guilds_with_campaigns(self) -> list[int]:
                return [THURSDAY.id, 555]  # 555 is a server the bot is not in

            async def forget_guild_if_empty(self, guild_id: int) -> bool:
                forgotten.append(guild_id)
                return True

        async def finish(n: Notice) -> None:
            finished.append(n)

        me = SimpleNamespace(
            web_delete_notices=Notes(), campaigns=Campaigns(), _finish_web_delete=finish
        )
        await DMBot.run_web_delete_notices(me)  # type: ignore[arg-type]
        self.assertEqual(
            (taken, finished, forgotten), ([THURSDAY.id, 555], [note], [THURSDAY.id, 555])
        )

    async def test_channels_of_a_server_the_bot_is_not_in_are_skipped_not_an_error(self) -> None:
        from dmbot.retention_channels import retire_channels

        note = Notice(555, "c1", "Gone", 1, (), 11, 22, 33, None, None, None, None)
        counts = await retire_channels(None, note, 0, 100, by_hand=True)
        self.assertEqual(counts.skipped, 3)
