"""The Story graph says which names have an NPC page (#2887), so the website can link to it now
that the Discord list is gone (#2874). `page` comes from the same source as the page's own door
(`npc_roster`, the players' view the bot writes), never from a second copy of the rule. Run as the
website's own database role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.dm_screen.npc_pages import NpcRosterStore
from dmbot.memory.recall import RecallService
from dmbot.web import npc_pages as page_door
from dmbot.web import relationships
from tests.test_recap_web import NOW
from tests.test_web_relationships import Web


class Pages(Web):
    async def list_them(self, campaign: str | None = None) -> int:
        """What the bot's pass writes: the players' view of the NPCs, kept in `npc_roster`."""
        cid = campaign or self.one.id
        entries = await RecallService(self.memory).roster(self.g, cid)
        await NpcRosterStore(self.db).save(self.g, cid, entries, NOW)
        return len(entries)

    def by_name(self, nodes: list[dict[str, Any]]) -> dict[str, bool]:
        return {str(n["name"]): n["page"] for n in nodes}

    async def test_a_listed_npc_has_a_page_in_a_players_graph(self) -> None:
        self.assertEqual(await self.list_them(), 1)  # Mira, the one confirmed NPC
        got = await relationships.read_web(self.web, self.player, self.cref, center=self.mira)
        marks = self.by_name(got["nodes"])
        self.assertTrue(marks["Mira Voss"])
        self.assertFalse(marks["Rook"])  # a player's character: no page
        self.assertFalse(marks["Greywater Keep"])  # a place: no page
        # and the page door agrees: it opens for exactly the names marked
        shown = await page_door.read_page(self.web, self.player, self.cref, self.mira)
        self.assertEqual(shown["name"], "Mira Voss")

    async def test_an_unlisted_entry_has_page_false(self) -> None:
        got = await relationships.read_web(self.web, self.player, self.cref, center=self.mira)
        self.assertEqual(set(self.by_name(got["nodes"]).values()), {False})  # nothing listed yet

    async def test_an_entry_hidden_from_players_has_no_page_in_any_graph(self) -> None:
        secret = await self.add("Baron Veil", "npc", hidden_from_players=True)
        await self.link("knows", self.mira, secret, "mira-veil")
        await self.list_them()
        for who in (self.player, self.session):
            got = await relationships.read_web(
                self.web, who, self.cref, center=self.mira, view="players"
            )
            marks = self.by_name(got["nodes"])
            self.assertNotIn("Baron Veil", marks)  # absent anyway in the players' view
            self.assertTrue(marks["Mira Voss"])
        full = await relationships.read_web(
            self.web, self.session, self.cref, center=self.mira, view="full"
        )
        self.assertFalse(self.by_name(full["nodes"])["Baron Veil"])  # a DM sees it, but no page

    async def test_a_secret_name_never_gets_a_page(self) -> None:
        hidden = await self.add("Ser Quiet", "npc")
        await self.sql("UPDATE memory_aliases SET secret = TRUE WHERE entity_id = %s", hidden)
        await self.link("knows", self.mira, hidden, "mira-quiet")
        await self.list_them()
        full = await relationships.read_web(
            self.web, self.session, self.cref, center=self.mira, view="full"
        )
        self.assertFalse(self.by_name(full["nodes"]).get("Ser Quiet", False))

    async def test_the_overview_marks_the_tray_and_the_group_cards(self) -> None:
        await self.list_them()
        lonely = await self.add("Brother Aldric", "npc")
        await self.list_them()
        got = await relationships.read_overview(self.web, self.player, self.cref)
        tray = {n["name"]: n["page"] for n in got["tray"]}
        self.assertTrue(tray["Brother Aldric"])  # listed, not connected
        self.assertFalse(tray["Dawnbreaker"])  # an item: no page
        self.assertEqual(lonely in {n["id"] for n in got["tray"]}, True)
        (group,) = got["groups"]
        self.assertEqual(group["page"], group["center"] == self.mira)  # the Keep is the centre
        self.assertFalse(group["page"])

    async def test_another_campaigns_entries_never_show(self) -> None:
        other = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Mira Voss", source="dm", status="confirmed"
        )
        await self.list_them(self.two.id)  # listed in campaign two only
        got = await relationships.read_web(self.web, self.session, self.cref, center=self.mira)
        self.assertNotIn(other.value.id, {n["id"] for n in got["nodes"]})
        self.assertFalse(self.by_name(got["nodes"])["Mira Voss"])  # one's Mira has no page

    async def test_the_version_door_gives_only_a_number_that_rises_with_every_graph_write(
        self,
    ) -> None:
        """#3294: the cheap read the Story Graph page asks first. A player gets it too (it says only
        that something changed), it carries nothing else, and a name added to the graph moves it."""
        before = await relationships.read_version(self.web, self.player, self.cref)
        self.assertEqual(set(before), {"version"})
        same = await relationships.read_version(self.web, self.session, self.cref)
        self.assertEqual(before, same)
        await self.memory.add_entity(
            self.g, self.one.id, type="npc", name="Harl Brandt", source="dm", status="confirmed"
        )
        after = await relationships.read_version(self.web, self.player, self.cref)
        self.assertGreater(after["version"], before["version"])

    async def test_the_version_door_refuses_an_outsider_and_another_campaign(self) -> None:
        from dmbot.web.recaps import RecapError
        from tests.test_recap_web import BOB
        from tests.test_web_api import QUILLON

        outsider = await self.signed(BOB, QUILLON)
        for who, ref in (
            (outsider, self.cref),
            (self.player, f"{self.g}.{'f' * 32}"),
        ):
            with self.assertRaises(RecapError) as caught:
                await relationships.read_version(self.web, who, ref)
            self.assertEqual(caught.exception.code, "not_found")

    async def test_the_size_door_asks_nothing_extra(self) -> None:
        got = await relationships.read_size(self.web, self.player, self.cref, center=self.mira)
        self.assertEqual(set(got), {"names"})
