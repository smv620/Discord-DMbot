"""A player's Story Graph is what their own characters know, worked out by the one gate (#3143):
the page equals `gate.visible_to` plus `display_names`, a player with two characters sees what
either sees (the "see as" of each, combined), nothing the gate hides is anywhere in the body, and
the four ways the old page (migration 0294) differed from the gate are pinned: a character added
late learns nothing of what was shown before (A), a statement limited to one character is not
shown to the others (B), a group's knowledge counts (C), and a row of 0.0 certainty counts (D).
Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory import gate
from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships, see_as
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import NOW
from tests.test_web_all_names import MEMBER
from tests.test_web_api import API, THURSDAY, FakeDiscord, settings
from tests.test_web_relationships import BOB_USER, Web

OTHER_PLAYER = 7777  # a player who is not signed in


class OneCampaign(Web):
    """Bob plays Okra, and later Late; someone else plays Wen. Nothing is public: each name and
    connection is told to exactly the characters the test names."""

    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.okra = await self.add(
            "Okra", "character", role="player_character", played_by=BOB_USER.id
        )
        self.wen = await self.add(
            "Wen", "character", role="player_character", played_by=OTHER_PLAYER
        )
        self.bruno = await self.add("Bruno", "character", role="npc", hidden_from_players=True)
        self.ken = await self.add("Ken", "character", role="npc", hidden_from_players=True)
        self.guild = await self.add("The Lantern Guild", "faction")
        self.limited = await self.add("Council Seat", "place")
        self.rumour = await self.add("Old Rumour", "place")
        self.guarded = await self.add("Guarded Vault", "place")
        sister = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", role="sibling", anchor_id=self.bruno
        )
        self.sister = sister.entity.id
        self.sister_link = await self.link("knows", self.mira, self.sister, "sister")
        await self.link("knows", self.mira, self.limited, "mira-council")
        await self.link("member_of", self.okra, self.guild, "okra-guild")
        await self.link("knows", self.mira, self.rumour, "mira-rumour")
        await self.link("knows", self.keep, self.guarded, "keep-vault")
        # start from nothing: each test says exactly who knows what (new things start out told to
        # the player characters, so this comes after everything exists)
        await self.sql("DELETE FROM memory_knowers WHERE campaign_id = %s", self.one.id)
        for who, statement, kind in (
            ([self.okra, self.wen], self.mira, ENTRY),
            ([self.okra, self.wen], self.keep, ENTRY),
            ([self.okra, self.wen], self.links["mira-keep"], CONNECTION),
            ([self.okra], self.sister, ENTRY),
            ([self.okra], self.sister_link, CONNECTION),
            ([self.wen], self.limited, ENTRY),  # B: limited to Wen
        ):
            await self.know(kind, statement, who)
        await self.know(CONNECTION, self.links["mira-council"], [self.wen])
        # C: the Guild knows the Old Rumour and the way to it; Okra is a member of the Guild
        await self.know(ENTRY, self.rumour, [self.guild])
        await self.know(CONNECTION, self.links["mira-rumour"], [self.guild])
        await self.know(CONNECTION, self.links["okra-guild"], [self.okra])
        await self.know(ENTRY, self.guild, [self.okra])
        # D: a row of 0.0 certainty is a row
        await self.know(ENTRY, self.guarded, {self.okra: 0.0})
        await self.know(CONNECTION, self.links["keep-vault"], {self.okra: 0.0})
        # a sleeping knower grants nothing
        await self.sql(
            "UPDATE memory_knowers SET hidden_by = ARRAY['x'] WHERE campaign_id = %s"
            " AND about_entity_id = %s",
            self.one.id,
            self.keep,
        )
        await self.know(ENTRY, self.keep, [self.wen])  # (and then only Wen knows the Keep)
        # a secret other name of Mira only Wen holds
        await self.alias(self.mira, "Lady Grey", secret=True)
        # A: a character Bob adds later learns nothing of what was shown before
        self.late = await self.add(
            "Late", "character", role="player_character", played_by=BOB_USER.id
        )
        self.everything = [
            self.mira, self.keep, self.bruno, self.ken, self.guild, self.limited, self.rumour,
            self.guarded, self.sister, self.okra, self.wen, self.late,
        ]  # fmt: skip

    async def know(self, kind: str, statement: str, who: Any) -> None:
        await self.memory.set_statement_knowers(
            self.g, self.one.id, kind, statement, who, source=DM_SOURCE
        )

    async def full_page(self, who: Any = None, **kw: Any) -> dict[str, Any]:
        return await self.graph(who or self.player, **kw)


class WhatTheGateSays(OneCampaign):
    async def test_the_names_are_the_gates(self) -> None:
        mine = gate.Viewer.player([self.okra, self.late])
        expected = await self.memory.display_names(self.g, self.one.id, mine, self.everything)
        async with self.memory._read(self.g, self.one.id) as scope:
            knows = await gate.visible_to(scope, mine)
        wanted = {
            i: name
            for i, name in expected.items()
            if name is not None and knows.knows(gate.ENTRY, i)
        }
        got = await self.full_page()
        shown = {n["id"]: n["name"] for n in got["nodes"] if not n["id"].startswith("alias:")}
        for i, name in shown.items():
            self.assertEqual(name, wanted[i])  # never a different name, never an extra one
        links = {x["id"] for e in got["edges"] for x in e["links"]}
        self.assertTrue(all(knows.knows(gate.CONNECTION, i) for i in links))

    async def test_nothing_the_gate_hides_is_anywhere_in_the_body(self) -> None:
        bodies = [str(await self.full_page(center=c, hops=8)) for c in (None, self.mira, self.okra)]
        bodies.append(str(await relationships.read_overview(self.web, self.player, self.cref)))
        bodies.append(str(await relationships.read_size(self.web, self.player, self.cref)))
        for body in bodies:
            for hidden in ("Bruno", "Ken", "Lady Grey", "Council Seat", "Wen"):
                self.assertNotIn(hidden, body)

    async def test_a_player_with_two_characters_sees_what_either_sees(self) -> None:
        together = {
            n["name"] for n in (await self.full_page(hops=8))["nodes"] if "alias:" not in n["id"]
        }
        each: set[str] = set()
        for character in (self.okra, self.late):
            got = await see_as.read_see_as(self.web, self.session, self.cref, character, hops=8)
            each |= {n["name"] for n in got["nodes"] if "alias:" not in n["id"]}
        # (a character with nothing to be joined to is on no map of its own)
        self.assertEqual(together, each - {"Late"})


class TheFourDifferences(OneCampaign):
    async def names(self, who: Any = None, **kw: Any) -> set[str]:
        got = await self.full_page(who, hops=8, **kw)
        return {n["name"] for n in got["nodes"]}

    async def test_a_a_character_added_late_knows_nothing_of_what_came_before(self) -> None:
        sees = await see_as.read_see_as(
            self.web, self.session, self.cref, self.late, center=self.mira, hops=8
        )
        self.assertEqual({n["name"] for n in sees["nodes"]} - {"Late"}, set())

    async def test_b_a_statement_limited_to_one_character_is_not_shown_to_the_others(self) -> None:
        self.assertNotIn("Council Seat", await self.names(center=self.mira))
        wen = await see_as.read_see_as(
            self.web, self.session, self.cref, self.wen, center=self.mira, hops=8
        )
        self.assertIn("Council Seat", {n["name"] for n in wen["nodes"]})

    async def test_c_what_a_group_knows_its_members_know(self) -> None:
        self.assertIn("Old Rumour", await self.names(center=self.mira))

    async def test_d_a_row_of_nothing_still_counts(self) -> None:
        self.assertIn("Guarded Vault", await self.names(center=self.mira))


class TheSplitNode(OneCampaign):
    async def test_a_secret_other_name_has_a_node_of_its_own_for_a_player_and_for_see_as(
        self,
    ) -> None:
        guard = await self.memory.add_unnamed(
            self.g, self.one.id, what="guard at the north gate", role="guard"
        )
        link = await self.link("knows", guard.entity.id, self.mira, "guard-mira", secret=True)
        await self.know(ENTRY, guard.entity.id, [self.okra])
        await self.know(CONNECTION, link, [self.okra])
        await self.memory.join_same_person(self.g, self.one.id, guard.entity.id, self.ken)
        label = guard.entity.name
        page = await self.full_page(center=self.mira, hops=3)
        sees = await see_as.read_see_as(
            self.web, self.session, self.cref, self.okra, center=self.mira, hops=3
        )
        for got in (page, sees):
            aliased = [n for n in got["nodes"] if n["id"].startswith("alias:")]
            self.assertEqual(len(aliased), 1)
            self.assertNotIn(self.ken, {n["id"] for n in got["nodes"]})
            self.assertNotIn("Ken", str(got))
            ends = {end for e in got["edges"] for end in (e["a"], e["b"])}
            self.assertIn(aliased[0]["id"], ends)
        self.assertEqual(
            {n["name"] for n in page["nodes"] if n["id"].startswith("alias:")},
            {n["name"] for n in sees["nodes"] if n["id"].startswith("alias:")},
        )
        self.assertIn(label, str(page))


class WhoIsLooking(OneCampaign):
    async def test_a_dm_in_players_view_is_the_party(self) -> None:
        got = await self.graph(self.session, view="players", hops=8)
        self.assertEqual(got["view"], "players")
        self.assertFalse(got["canEdit"])
        party = await see_as.read_see_as(self.web, self.session, self.cref, see_as.PARTY, hops=8)
        self.assertEqual({n["id"] for n in got["nodes"]}, {n["id"] for n in party["nodes"]})

    async def test_a_peek_is_still_the_dm_screen(self) -> None:
        peeked = await self.graph(self.player, view="full", hops=8)
        self.assertEqual(peeked["view"], "full")
        overview = await relationships.read_overview(self.web, self.player, self.cref, view="full")
        self.assertIn("Ken", {n["name"] for n in overview["tray"]})

    async def test_a_member_with_no_character_gets_an_empty_page_and_a_stranger_nothing(
        self,
    ) -> None:
        nobody = await self.signed(
            type(BOB_USER)(id=4242, name="Nobody", email="n@example.org"), MEMBER
        )
        got = await self.graph(nobody)
        self.assertEqual((got["nodes"], got["edges"]), ([], []))
        with self.assertRaises(RecapError) as caught:
            await relationships.read_web(self.web, self.player, f"999.{self.one.id}")
        self.assertEqual(caught.exception.code, "not_found")

    async def test_the_characters_come_from_who_plays_them_not_from_the_request(self) -> None:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        from dmbot.web import sessions

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        ) as client:  # fmt: skip
            token = await sessions.sign_in(self.web, BOB_USER, [THURSDAY], now=NOW, days=30)
            client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            path = f"/me/campaigns/{self.cref}/web"
            plain = await client.get(path, params={"hops": "8"})
            forged = await client.get(
                path, params={"hops": "8", "viewer": self.wen, "characters": self.wen}
            )
            see = await client.get(f"{path}/see-as", params={"viewer": self.wen})
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(plain.json(), forged.json())
        self.assertEqual(see.status_code, 403)  # (a picker is the DM's alone)
        self.assertNotIn("Council Seat", plain.text)
