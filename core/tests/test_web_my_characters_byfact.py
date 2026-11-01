"""The split is by fact (Supervisor, #3037): a join records the secret name each moved connection
came through, and that decides WHERE a connection a player's characters know is drawn, never
WHETHER they know it. Product reveals throughout. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships
from tests.test_recap_web import BOB
from tests.test_web_my_characters_view import MyCharacters


class ByFactBase(MyCharacters):
    async def reveal(self, entity: str) -> None:
        await self.memory.set_hidden_from_players(self.g, self.one.id, entity, False, source="dm")

    async def guard_alias(self, owner: str | None = None) -> Any:
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=owner or self.ken, include_secret=True
        )
        return next(a for a in found if a.text == self.label)

    def edges_of(self, got: dict[str, Any]) -> dict[str, tuple[str, str]]:
        return {x["id"]: (e["a"], e["b"]) for e in got["edges"] for x in e["links"]}

    async def mine(self) -> list[tuple[str, str, str]]:
        m = await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)
        return [(c.subject, c.predicate, c.object) for c in m.connections]

    async def report(self) -> dict[str, Any]:
        graph = await self.bob()
        wide = await self.graph(self.player, hops=8)
        overview, _pages, _known = await relationships._overview_door(
            self.web, self.player, self.cref, None, 100, 100
        )
        return {
            "graph_nodes": self.names(graph),
            "graph_links": self.edges_of(graph),
            "wide_nodes": self.names(wide),
            "wide_links": self.edges_of(wide),
            "ov_names": {n["id"]: n["name"] for n in overview["names"]},
            "ov_pairs": overview["pairs"],
            "mine": await self.mine(),
        }

    def on_the_guard_not_ken(self, r: dict[str, Any]) -> None:
        for key in ("graph_links", "wide_links"):
            ends = r[key].get(self.guard_link)
            self.assertIsNotNone(ends, key)
            assert ends is not None
            self.assertNotIn(self.ken, ends, key)
            self.assertTrue(any(str(x).startswith("alias:") for x in ends), key)
        self.assertIn((self.label, "knows", "Mira Voss"), r["mine"])
        self.assertNotIn(("Ken", "knows", "Mira Voss"), r["mine"])
        for pair in r["ov_pairs"]:
            self.assertNotEqual(sorted(pair), sorted([self.ken, self.mira]))


class Reveals(ByFactBase):
    async def test_a_ken_revealed_before_the_join_is_still_two_people(self) -> None:
        await self.reveal(self.ken)
        await self.join()
        self.on_the_guard_not_ken(await self.report())

    async def test_a_ken_revealed_after_the_join_is_still_two_people(self) -> None:
        await self.join()
        await self.reveal(self.ken)
        self.on_the_guard_not_ken(await self.report())

    async def test_a_ken_public_from_creation_keeps_the_guards_fact_off_him(self) -> None:
        bran = await self.add("Bran", "character", role="npc")  # shown to everyone at once
        hooded = await self.memory.add_unnamed(self.g, self.one.id, what="hooded man", role="guard")
        link = await self.link("knows", hooded.entity.id, self.keep, "hm-keep", secret=True)
        await self.know(ENTRY, hooded.entity.id, [self.okra])
        await self.know(CONNECTION, link, [self.okra])
        table_wide = await self.link("ally_of", bran, self.mira, "bran-mira")
        await self.memory.join_same_person(self.g, self.one.id, hooded.entity.id, bran)
        got = await self.graph(self.player, center=self.mira, hops=3)
        links = self.edges_of(got)
        self.assertNotIn(bran, links.get(link, ()))
        self.assertIn(bran, links.get(table_wide, ()))  # (what everyone knows stays on Bran)
        self.assertFalse(
            any(s == "Bran" and o == "Greywater Keep" for s, _, o in await self.mine())
        )

    async def test_the_fold_and_the_undo_of_both(self) -> None:
        await self.reveal(self.ken)
        before = await self.report()
        written = await self.join()
        alias = await self.guard_alias()
        fold = await self.memory.make_name_not_secret(
            self.g, self.one.id, alias.id, source=DM_SOURCE
        )
        folded = await self.report()
        self.assertEqual(set(folded["graph_links"][self.guard_link]), {self.ken, self.mira})
        self.assertFalse(any(str(i).startswith("alias:") for i in folded["graph_nodes"]))
        assert fold.batch is not None and written.batch is not None
        await self.memory.undo(self.g, self.one.id, fold.batch, source=DM_SOURCE)
        await self.memory.undo(self.g, self.one.id, written.batch, source=DM_SOURCE)
        row = await self.sql_one(
            "SELECT via_alias_id FROM memory_relations WHERE id = %s", self.guard_link
        )
        self.assertIsNone(row["via_alias_id"])
        after = await self.report()
        self.assertEqual(after["graph_nodes"], before["graph_nodes"])
        self.assertEqual(after["mine"], before["mine"])


class NeverWiderThanWhatTheyKnow(ByFactBase):
    """`via_alias_id` decides where a known fact is drawn, never whether it is theirs."""

    async def test_another_players_secret_link_of_the_guard_does_not_come_to_okra(self) -> None:
        link = await self.link("serves", self.guard, self.keep, "g-keep-wen", secret=True)
        await self.know(CONNECTION, link, [self.wen])
        await self.join()
        r = await self.report()
        self.assertNotIn(link, r["graph_links"])
        self.assertNotIn(link, r["wide_links"])
        self.assertFalse(any(o == "Greywater Keep" for _, p, o in r["mine"] if p != "knows"))

    async def test_a_link_nobody_knows_does_not_come_to_okra(self) -> None:
        link = await self.link("serves", self.guard, self.keep, "g-keep-none", secret=True)
        await self.know(CONNECTION, link, [])
        await self.join()
        self.assertNotIn(link, (await self.report())["wide_links"])

    async def test_an_unrevealed_link_of_the_guard_is_as_hidden_as_before(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.guard, "serves", self.keep, source=DM_SOURCE,
            confidence=1.0, status="confirmed", unrevealed=True,
        )  # fmt: skip
        link = made.value[0].id
        before = (await self.report())["wide_links"]
        await self.join()
        self.assertEqual(link in (await self.report())["wide_links"], link in before)

    async def test_a_table_wide_link_of_the_guard_does_not_appear_on_a_node_of_the_guard(
        self,
    ) -> None:
        link = await self.link("serves", self.guard, self.keep, "g-keep-pub")
        await self.join()
        r = await self.report()
        self.assertFalse(
            any(str(x).startswith("alias:") for x in r["wide_links"].get(link, ())),
        )

    async def test_the_guard_only_another_player_knew_shows_okra_nothing(self) -> None:
        await self.know(ENTRY, self.guard, [self.wen])
        await self.know(CONNECTION, self.guard_link, [self.wen])
        await self.join()
        r = await self.report()
        self.assertNotIn(self.guard_link, r["wide_links"])
        self.assertNotIn(self.label, str(r))


class WhenTheNameChanges(ByFactBase):
    async def test_a_plain_merge_of_ken_keeps_the_connection_on_the_guard(self) -> None:
        kenneth = await self.add("Kenneth", "character", role="npc")
        await self.join()
        await self.memory.merge(
            self.g, self.one.id, kenneth, self.ken, source=DM_SOURCE, dm_said_same=True
        )
        alias = await self.guard_alias(kenneth)
        row = await self.sql_one(
            "SELECT via_alias_id FROM memory_relations WHERE id = %s", self.guard_link
        )
        self.assertEqual(row["via_alias_id"], alias.id)
        r = await self.report()
        self.assertNotIn(kenneth, r["wide_links"].get(self.guard_link, ()))

    async def test_a_name_that_folds_into_a_twin_leaves_the_connection_not_shown(self) -> None:
        kenneth = await self.add("Kenneth", "character", role="npc")
        await self.join()
        await self.alias(kenneth, self.label, secret=True)
        await self.memory.merge(
            self.g, self.one.id, kenneth, self.ken, source=DM_SOURCE, dm_said_same=True
        )
        r = await self.report()
        self.assertNotIn(kenneth, r["wide_links"].get(self.guard_link, ()))

    async def test_a_rejected_name_does_not_send_the_connection_to_ken(self) -> None:
        await self.reveal(self.ken)
        await self.join()
        alias = await self.guard_alias()
        await self.memory.update_alias(
            self.g, self.one.id, alias.id, source=DM_SOURCE, status="rejected"
        )
        r = await self.report()
        self.assertNotIn(self.ken, r["wide_links"].get(self.guard_link, ()))
        self.assertNotIn(("Ken", "knows", "Mira Voss"), r["mine"])

    async def test_clearing_who_holds_the_name_does_not_send_it_to_ken(self) -> None:
        await self.reveal(self.ken)
        await self.join()
        alias = await self.guard_alias()
        await self.memory.set_alias_knowers(self.g, self.one.id, alias.id, [], source=DM_SOURCE)
        await self.sql("DELETE FROM memory_knowers WHERE alias_id = %s", alias.id)
        r = await self.report()
        self.assertNotIn(self.ken, r["wide_links"].get(self.guard_link, ()))
        self.assertNotIn(("Ken", "knows", "Mira Voss"), r["mine"])

    async def test_another_name_merged_into_ken_changes_nothing(self) -> None:
        other = await self.add("Kenny", "character", role="npc")
        await self.join()
        await self.memory.merge(
            self.g, self.one.id, self.ken, other, source=DM_SOURCE, dm_said_same=True
        )
        self.on_the_guard_not_ken(await self.report())

    async def test_a_second_join_onto_ken_has_its_own_node(self) -> None:
        await self.join()
        beggar = await self.memory.add_unnamed(self.g, self.one.id, what="beggar", role="guard")
        link = await self.link("knows", beggar.entity.id, self.keep, "beg-keep", secret=True)
        await self.know(ENTRY, beggar.entity.id, [self.okra])
        await self.know(CONNECTION, link, [self.okra])
        await self.memory.join_same_person(self.g, self.one.id, beggar.entity.id, self.ken)
        await self.reveal(self.ken)
        r = await self.report()
        self.assertNotEqual(r["wide_links"][link][0], r["wide_links"][self.guard_link][0])


class TheDmsViews(ByFactBase):
    async def test_the_dm_sees_one_entry_in_both_views(self) -> None:
        await self.reveal(self.ken)
        await self.join()
        full = await self.graph(center=self.mira, hops=3)
        players = await self.graph(center=self.mira, hops=3, view="players")
        for got in (full, players):
            self.assertFalse(any(str(i).startswith("alias:") for i in self.names(got)))
        self.assertEqual(set(self.edges_of(full)[self.guard_link]), {self.ken, self.mira})


class TheNamesList(ByFactBase):
    """A joined name is a person of its own: never listed as "a name of Ken", whether the character
    knows any of its connections or not, and whether Ken is public or hidden."""

    async def names_listed(self) -> list[str]:
        mine = await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)
        return [n.text for n in mine.names]

    async def forget_the_guards_link(self) -> None:
        await self.sql("DELETE FROM memory_knowers WHERE relation_id = %s", self.guard_link)

    async def test_a_held_name_with_no_known_connection_is_not_listed_with_ken_hidden(
        self,
    ) -> None:
        await self.join()
        await self.forget_the_guards_link()
        self.assertEqual(await self.names_listed(), [])

    async def test_a_held_name_with_no_known_connection_is_not_listed_with_ken_public(
        self,
    ) -> None:
        await self.reveal(self.ken)
        await self.join()
        await self.forget_the_guards_link()
        self.assertEqual(await self.names_listed(), [])
        mine = await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)
        self.assertNotIn("Ken", repr(mine))
