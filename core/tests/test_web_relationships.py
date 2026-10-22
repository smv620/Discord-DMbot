"""The relationship web's API (#2323), run as the website's own database role: the names within N
steps of one name and the confirmed connections among them. A DM gets everything (secret flagged);
any other member of the server gets the players' view; anyone else, another campaign or a name the
caller may not see is "not found". Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import httpx

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, callers
from tests.test_web_all_names import MEMBER, Listing
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class Web(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Mira (npc) knows the Keep and is an ally of Rook; Rook is in the Keep; the Keep is in the
        # Far Hall, two steps from Mira.
        self.rook = await self.add("Rook", "character", role="player_character")
        self.hall = await self.add("Far Hall", "place")
        self.links: dict[str, str] = {}
        await self.link("knows", self.mira, self.keep, "mira-keep")
        await self.link("ally_of", self.mira, self.rook, "mira-rook")
        await self.link("located_in", self.rook, self.keep, "rook-keep")
        await self.link("located_in", self.keep, self.hall, "keep-hall")
        self.player = await self.signed(BOB, MEMBER)

    async def link(self, predicate: str, a: str, b: str, tag: str, *, secret: bool = False) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, a, predicate, b, source=DM_SOURCE, confidence=1.0,
            status="confirmed", secret=secret,
        )  # fmt: skip
        self.links[tag] = made.value[0].id
        return made.value[0].id

    async def graph(self, who: Any = None, **kw: Any) -> dict[str, Any]:
        return await relationships.read_web(self.web, who or self.session, self.cref, **kw)

    async def refused_web(self, who: Any, ref: str | None = None, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await relationships.read_web(self.web, who, ref or self.cref, **kw)
        return caught.exception.code


class TheShape(Web):
    async def test_the_names_within_the_steps_and_the_connections_among_them(self) -> None:
        got = await self.graph(center=self.mira, hops=1)
        self.assertEqual(got["center"], self.mira)
        self.assertEqual({n["name"] for n in got["nodes"]}, {"Mira Voss", "Greywater Keep", "Rook"})
        self.assertEqual(got["maxHops"], relationships.MAX_HOPS)
        self.assertTrue(got["canEdit"])
        self.assertFalse(got["truncated"])
        pairs = {(e["a"], e["b"]) for e in got["edges"]}
        self.assertEqual(len(pairs), 3)  # Mira-Keep, Mira-Rook, Rook-Keep; not the Far Hall
        two = await self.graph(center=self.mira, hops=2)
        self.assertEqual(len(two["nodes"]), 4)
        self.assertEqual(len(two["edges"]), 4)

    async def test_the_nearest_names_come_first_and_a_node_says_its_kind(self) -> None:
        got = await self.graph(center=self.mira, hops=2)
        self.assertEqual(got["nodes"][0]["id"], self.mira)  # the centre first
        by_name = {n["name"]: n for n in got["nodes"]}
        self.assertEqual(by_name["Rook"]["kind"], "player_character")
        self.assertEqual(by_name["Mira Voss"]["kind"], "npc")
        self.assertEqual(by_name["Far Hall"]["kind"], "place")

    async def test_no_centre_is_the_most_connected_name(self) -> None:
        got = await self.graph(hops=1)
        self.assertEqual(got["center"], self.keep)  # three connections

    async def test_an_edge_is_labelled_by_the_latest_known_connection(self) -> None:
        await self.link("enemy_of", self.mira, self.keep, "old-feud")  # a second, later one
        await self.link("owns", self.mira, self.keep, "secret-owner", secret=True)  # newest, secret
        got = await self.graph(center=self.mira, hops=1)
        edge = next(e for e in got["edges"] if {e["a"], e["b"]} == {self.mira, self.keep})
        self.assertEqual(edge["label"], "is an enemy of")  # latest known; never the secret one
        self.assertEqual(edge["more"], 2)
        by_id = {x["id"]: x for x in edge["links"]}
        self.assertEqual(len(by_id), 3)
        self.assertTrue(by_id[self.links["secret-owner"]]["secret"])  # a DM is told which
        self.assertFalse(by_id[self.links["mira-keep"]]["secret"])
        self.assertEqual(by_id[self.links["old-feud"]]["direction"], "both")  # reads either way
        self.assertEqual(by_id[self.links["mira-keep"]]["words"], "knows")

    async def test_direction_says_which_way_the_words_read(self) -> None:
        got = await self.graph(center=self.keep, hops=1)
        edge = next(e for e in got["edges"] if {e["a"], e["b"]} == {self.rook, self.keep})
        link = edge["links"][0]
        self.assertEqual(link["words"], "is in")
        # Rook is in the Keep: the words read from the subject (Rook) to the object (Keep)
        expected = "a-b" if edge["a"] == self.rook else "b-a"
        self.assertEqual(link["direction"], expected)

    async def test_only_confirmed_connections_between_confirmed_names_are_drawn(self) -> None:
        stranger = await self.add("Stranger", "npc")
        await self.link("knows", self.mira, stranger, "kept")
        await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", self.sword, source=DM_SOURCE,
            confidence=0.5, status="proposed",
        )  # fmt: skip
        got = await self.graph(center=self.mira, hops=1)
        names = {n["name"] for n in got["nodes"]}
        self.assertIn("Stranger", names)
        self.assertNotIn("Dawnbreaker", names)  # only a proposed connection

    async def test_the_size_counts_the_names_before_drawing(self) -> None:
        self.assertEqual(
            await relationships.read_size(
                self.web, self.session, self.cref, center=self.mira, hops=1
            ),
            {"names": 3},
        )
        self.assertEqual(
            (
                await relationships.read_size(
                    self.web, self.session, self.cref, center=self.mira, hops=2
                )
            )["names"],
            4,
        )

    async def test_past_the_cap_the_nearest_are_kept_and_it_says_so(self) -> None:
        with patch.object(relationships, "MAX_NODES", 2):
            got = await self.graph(center=self.mira, hops=2)
        self.assertTrue(got["truncated"])
        self.assertEqual(len(got["nodes"]), 2)
        self.assertEqual(got["nodes"][0]["id"], self.mira)
        ids = {n["id"] for n in got["nodes"]}
        self.assertTrue(all({e["a"], e["b"]} <= ids for e in got["edges"]))
        with patch.object(relationships, "MAX_NODES", 2):
            size = await relationships.read_size(
                self.web, self.session, self.cref, center=self.mira, hops=2
            )
        self.assertEqual(size["names"], 4)  # the size is the whole, not the cap


class WhoSeesWhat(Web):
    async def test_a_member_never_gets_a_secret_connection_or_name(self) -> None:
        await self.link("owns", self.mira, self.hall, "secret-link", secret=True)
        lady = await self.add("Lady Grey", "npc")
        await self.alias(lady, "Lady Grey", secret=True)  # its own name is a secret
        await self.link("knows", self.mira, lady, "to-secret-name")
        dm = await self.graph(center=self.mira, hops=1)
        self.assertIn("Lady Grey", {n["name"] for n in dm["nodes"]})
        flagged = {n["name"]: n.get("secret") for n in dm["nodes"]}
        self.assertTrue(flagged["Lady Grey"])  # a DM is told
        player = await self.graph(self.player, center=self.mira, hops=2)
        self.assertNotIn("Lady Grey", {n["name"] for n in player["nodes"]})
        self.assertFalse(player["canEdit"])
        every_link = [x["id"] for e in player["edges"] for x in e["links"]]
        self.assertNotIn(self.links["secret-link"], every_link)
        self.assertNotIn(self.links["to-secret-name"], every_link)
        self.assertTrue(all("secret" not in n for n in player["nodes"]))  # the flag is a DM's
        self.assertTrue(all(x["secret"] is False for e in player["edges"] for x in e["links"]))

    async def test_a_member_does_not_get_what_the_players_do_not_know_yet(self) -> None:
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", self.hall
        )
        player = await self.graph(self.player, center=self.mira, hops=2)
        self.assertNotIn("Far Hall", {n["name"] for n in player["nodes"]})
        dm = await self.graph(center=self.mira, hops=2)
        self.assertIn("Far Hall", {n["name"] for n in dm["nodes"]})
        self.assertEqual(await self.refused_web(self.player, center=self.hall, hops=1), "not_found")

    async def test_thread_like_kinds_are_not_for_members(self) -> None:
        quest = await self.add("Find the Lost Bell", "quest")
        await self.link("knows", self.mira, quest, "quest-link")
        player = await self.graph(self.player, center=self.mira, hops=1)
        self.assertNotIn("Find the Lost Bell", {n["name"] for n in player["nodes"]})

    async def test_a_label_for_a_member_is_never_the_secret_one(self) -> None:
        await self.link("owns", self.mira, self.keep, "newest-secret", secret=True)
        player = await self.graph(self.player, center=self.mira, hops=1)
        edge = next(e for e in player["edges"] if {e["a"], e["b"]} == {self.mira, self.keep})
        self.assertEqual(edge["label"], "knows")
        self.assertEqual(edge["more"], 0)  # the secret one is not even counted


class Refusals(Web):
    async def test_someone_outside_the_server_another_campaign_or_a_missing_name(self) -> None:
        elsewhere = await self.signed(BOB, MEMBER.__class__(id=999, name="Elsewhere", manage=True))
        self.assertEqual(await self.refused_web(elsewhere, center=self.mira), "not_found")
        self.assertEqual(
            await self.refused_web(self.session, f"{self.g}.nosuchcampaign", center=self.mira),
            "not_found",
        )
        self.assertEqual(await self.refused_web(self.session, "nonsense"), "not_found")
        other_campaign_name = await self.add("Elsewhere Name", "npc")
        await self.sql("DELETE FROM memory_entities WHERE id = %s", other_campaign_name)
        self.assertEqual(await self.refused_web(self.session, center="f" * 32), "not_found")
        self.assertEqual(await self.refused_web(self.session, center="not-an-id"), "not_found")

    async def test_the_steps_are_one_to_the_most(self) -> None:
        for hops in (0, relationships.MAX_HOPS + 1, -1):
            self.assertEqual(
                await self.refused_web(self.session, center=self.mira, hops=hops), "bad_hops"
            )

    async def test_a_campaign_with_no_connections_draws_nothing_or_its_one_name(self) -> None:
        empty = await self.campaigns.create(self.g, "Empty", 1001)
        got = await relationships.read_web(self.web, self.session, f"{self.g}.{empty.id}")
        self.assertEqual((got["center"], got["nodes"], got["edges"]), (None, [], []))

    def test_only_the_relationship_code_calls_the_door(self) -> None:
        self.assertEqual(callers("dmbot_web_relationships("), {"schema.py", "web/relationships.py"})


class TheRoute(Web):
    async def client(self, who: Any) -> httpx.AsyncClient:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        )  # fmt: skip
        token = await self.token_of(who)
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def token_of(self, who: Any) -> str:
        user, guilds = (BOB, [MEMBER]) if who is self.player else (ALICE, [THURSDAY])
        return await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)

    async def test_the_routes_answer_and_a_bad_step_count_is_a_plain_400(self) -> None:
        async with await self.client(self.session) as client:
            got = await client.get(
                f"/me/campaigns/{self.cref}/web", params={"center": self.mira, "hops": "1"}
            )
            self.assertEqual(got.status_code, 200)
            self.assertEqual(got.json()["center"], self.mira)
            size = await client.get(
                f"/me/campaigns/{self.cref}/web/size", params={"center": self.mira, "hops": "2"}
            )
            self.assertEqual(size.json(), {"names": 4})
            for hops in ("x", "99", "-1", "0"):
                bad = await client.get(f"/me/campaigns/{self.cref}/web", params={"hops": hops})
                self.assertEqual((bad.status_code, bad.json()["error"]), (400, "bad_hops"), hops)
            gone = await client.get(f"/me/campaigns/{self.g}.nope/web")
            self.assertEqual(gone.status_code, 404)


if __name__ == "__main__":
    unittest.main()
