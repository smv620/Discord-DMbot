"""The owner's own campaigns in any server, and deleting one from the website (#2554, part 1): the
owner sees a campaign in a server the session's list does not have, a game master who is not the
owner does not, and the delete follows Discord's rule (the owner; with no owner any game master who
can reach the server; the typed name; not during a session)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx

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
        keep = {
            g: await self.campaigns.campaign_ids(g)
            for g in await self.campaigns.guilds_with_campaigns()
        }
        self.assertEqual(self.audio.sweep_gone(keep, min_age_s=0), 1)
        self.assertFalse((self.audio_root / str(LOST) / self.lost.id).exists())

    async def test_the_sweep_keeps_live_campaigns_and_new_folders(self) -> None:
        mine = self.audio_root / str(THURSDAY.id) / self.one.id / "recaps/1"
        mine.mkdir(parents=True)
        new = self.audio_root / str(LOST) / "brandnew"
        new.mkdir(parents=True)
        keep = {THURSDAY.id: {self.one.id, self.two.id}}
        self.assertEqual(self.audio.sweep_gone(keep), 0)  # nothing is a day old
        self.assertEqual(self.audio.sweep_gone(keep, min_age_s=0), 1)  # only the stray goes
        self.assertTrue(mine.exists())
        self.assertFalse(new.exists())

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
