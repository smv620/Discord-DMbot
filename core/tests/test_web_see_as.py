"""The DM's story graph seen as the party, a player character or an NPC (#2999): the DM's graph with
everything the viewer does not know removed, by the same gate the bot uses. DM only. Invented
names."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import see_as, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import API, THURSDAY, FakeDiscord, settings
from tests.test_web_relationships import Web


class SeeAs(Web):
    """Rook (a player character) knows Mira and her friendship with Rook; the Keep is the DM's
    alone. Okra is a second player character who knows nothing yet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.okra = await self.add("Okra", "character", role="player_character")
        # (the fixture's characters start out knowing what the DM entered; start from nothing so
        # each test says exactly who knows what)
        async with self.db.guild(self.g) as conn:
            await conn.execute("DELETE FROM memory_knowers WHERE campaign_id = %s", (self.one.id,))
        await self.know(ENTRY, self.mira, [self.rook])
        await self.know(CONNECTION, self.links["mira-rook"], [self.rook])

    async def know(self, kind: str, statement: str, who: list[str]) -> None:
        await self.memory.set_statement_knowers(
            self.g, self.one.id, kind, statement, who, source=DM_SOURCE
        )

    async def seen(self, viewer: str, who: Any = None, **kw: Any) -> dict[str, Any]:
        return await see_as.read_see_as(
            self.web, who or self.session, self.cref, viewer, center=self.mira, hops=3, **kw
        )

    async def refused_see_as(self, viewer: str, who: Any = None, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await see_as.read_see_as(self.web, who or self.session, ref or self.cref, viewer)
        return caught.exception.code

    @staticmethod
    def names(got: dict[str, Any]) -> set[str]:
        return {n["name"] for n in got["nodes"]}


class WhatACharacterSees(SeeAs):
    async def test_a_player_character_sees_what_it_knows_and_nothing_else(self) -> None:
        got = await self.seen(self.rook)
        self.assertEqual(self.names(got), {"Rook", "Mira Voss"})
        (edge,) = got["edges"]
        self.assertEqual({edge["a"], edge["b"]}, {self.rook, self.mira})
        self.assertEqual([x["id"] for x in edge["links"]], [self.links["mira-rook"]])
        self.assertEqual(got["seeAs"], {"viewer": self.rook, "name": "Rook"})
        self.assertFalse(got["canEdit"])
        self.assertNotIn("Greywater Keep", str(got))

    async def test_an_npc_is_a_point_of_view_too(self) -> None:
        await self.know(ENTRY, self.keep, [self.mira])
        await self.know(CONNECTION, self.links["mira-keep"], [self.mira])
        got = await self.seen(self.mira)
        self.assertEqual(self.names(got), {"Mira Voss", "Greywater Keep"})
        self.assertEqual(got["seeAs"]["name"], "Mira Voss")

    async def test_the_party_sees_only_what_every_player_character_knows(self) -> None:
        alone = await self.seen(see_as.PARTY)
        self.assertEqual(self.names(alone), set())  # (Okra knows nothing of Mira)
        await self.know(ENTRY, self.mira, [self.rook, self.okra])
        await self.know(ENTRY, self.rook, [self.rook, self.okra])
        await self.know(CONNECTION, self.links["mira-rook"], [self.rook, self.okra])
        together = await self.seen(see_as.PARTY)
        self.assertEqual(self.names(together), {"Rook", "Mira Voss"})
        self.assertEqual(together["seeAs"]["name"], "The party")

    async def test_the_dm_marks_are_removed(self) -> None:
        secret = await self.link("knows", self.mira, self.rook, "secret", secret=True)
        await self.know(CONNECTION, secret, [self.rook])
        got = await self.seen(self.rook)
        links = [x for e in got["edges"] for x in e["links"]]
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertEqual(
                (link["secret"], link["revealed"], link["pinned"], link["check"]),
                (False, True, False, ""),
            )
            self.assertNotIn("who", link)
            self.assertNotIn("knownBy", link)
        for node in got["nodes"]:
            self.assertTrue(node["revealed"])
            self.assertIsNone(node["secret"])


class AMainNameStaysSecret(SeeAs):
    async def test_knowing_a_connection_does_not_give_away_a_main_name(self) -> None:
        # Rook knows the Keep and Mira's link to it exists only through "mira-keep"; Rook does not
        # know Mira under her own name (no row of its own for her), only the connection.
        await self.know(ENTRY, self.mira, [])
        await self.know(CONNECTION, self.links["mira-keep"], [self.rook])
        got = await self.seen(self.rook)
        self.assertNotIn("Mira Voss", str(got))
        self.assertEqual(got["edges"], [])  # an edge to a name nobody may be shown is not drawn

    async def test_it_is_shown_by_the_other_name_the_viewer_knows(self) -> None:
        await self.know(ENTRY, self.mira, [])
        await self.know(CONNECTION, self.links["mira-keep"], [self.rook])
        made = await self.memory.add_alias(
            self.g, self.one.id, self.mira, "the ferrywoman",
            kind="nickname", source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        alias = made.value.id
        await self.know(ALIAS, alias, [self.rook])
        got = await self.seen(self.rook)
        self.assertIn("the Ferrywoman", self.names(got))
        self.assertNotIn("Mira Voss", str(got))


class WhoMayLook(SeeAs):
    async def test_a_player_is_refused_and_so_is_anyone_in_another_campaign(self) -> None:
        self.assertEqual(await self.refused_see_as(self.rook, self.player), "not_allowed")
        self.assertEqual(
            await self.refused_see_as(self.rook, ref=f"{THURSDAY.id}.{'0' * 32}"), "not_allowed"
        )

    async def test_a_name_that_is_not_a_character_here_is_not_found(self) -> None:
        for viewer in ("0" * 32, self.keep, "nonsense"):  # (a place is not a point of view)
            with self.subTest(viewer):
                self.assertEqual(await self.refused_see_as(viewer), "not_found")

    async def test_the_door_answers_nothing_to_a_player_even_by_sql(self) -> None:
        async with self.web.user(BOB.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_knowers_data(%s, %s) AS r", (THURSDAY.id, self.one.id)
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["r"])


class NeverMoreThanThePlayerSees(SeeAs):
    async def test_seeing_as_a_player_character_is_inside_that_players_own_view(self) -> None:
        # Bob plays Okra. Okra knows Mira and her link to the Keep, and nothing else. The player's
        # own page still adds what the table is shown by the older flags (the Keep, Rook), so the
        # two are not equal yet; this view must never show MORE than the player is shown.
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET played_by = %s WHERE id = %s", (BOB.id, self.okra)
            )
        await self.know(ENTRY, self.mira, [self.okra])
        await self.know(ENTRY, self.keep, [self.okra])
        await self.know(CONNECTION, self.links["mira-keep"], [self.okra])
        theirs = await self.graph(self.player, center=self.mira, hops=3)
        ours = await self.seen(self.okra)
        self.assertEqual(self.names(ours), {"Mira Voss", "Greywater Keep"})
        self.assertLessEqual(
            {(n["id"], n["name"]) for n in ours["nodes"]},
            {(n["id"], n["name"]) for n in theirs["nodes"]},
        )
        self.assertLessEqual(
            {(e["a"], e["b"], tuple(x["id"] for x in e["links"])) for e in ours["edges"]},
            {(e["a"], e["b"], tuple(x["id"] for x in e["links"])) for e in theirs["edges"]},
        )


class TheRoute(SeeAs):
    async def client(self, user: Any) -> httpx.AsyncClient:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        )  # fmt: skip
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def test_the_dm_gets_the_view_and_a_player_does_not(self) -> None:
        from tests.test_web_api import ALICE

        path = f"/me/campaigns/{self.cref}/web/see-as"
        async with await self.client(ALICE) as client:
            ok = await client.get(path, params={"viewer": self.rook, "center": self.mira})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual({n["name"] for n in ok.json()["nodes"]}, {"Rook", "Mira Voss"})
            missing = await client.get(path, params={"viewer": "0" * 32})
            self.assertEqual(missing.status_code, 404)
            nothing = await client.get(path)
            self.assertEqual(nothing.status_code, 422)
        async with await self.client(BOB) as client:
            refused = await client.get(path, params={"viewer": self.rook})
            self.assertEqual(refused.status_code, 403)
