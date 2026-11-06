"""A signed-in player sees what their own characters know on the website's Story Graph and its
overview, under the names those characters know (#3037, slice B): after "the guard at the north
gate" is joined with "Ken" (who is hidden from the players), a player whose character knew only
the guard sees the guard, never Ken; one whose character knew Ken sees Ken; another player's
knowledge never shows; the DM sees the main name; a peek behind the DM screen and the table-wide
view are as before; Undo gives each back what they saw. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.web import relationships
from tests.test_recap_web import BOB
from tests.test_web_all_names import MEMBER
from tests.test_web_api import ALICE
from tests.test_web_relationships import BOB_USER, Web

OTHER_PLAYER = 7777  # a player who is not signed in


class MyCharacters(Web):
    bob_has_a_hero = False  # (these tests give Bob his own characters)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Bob plays Okra; someone else plays Wen. Ken is hidden from the players.
        self.okra = await self.add("Okra", "character", role="player_character", played_by=BOB.id)
        self.wen = await self.add(
            "Wen", "character", role="player_character", played_by=OTHER_PLAYER
        )
        self.ken = await self.add("Ken", "character", role="npc", hidden_from_players=True)
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        (shown,) = await self.memory.unnamed(self.g, self.one.id)
        self.label = shown[0].name
        # The guard knows Mira, secretly; Okra knows both the guard and that.
        self.guard_link = await self.link("knows", self.guard, self.mira, "guard-mira", secret=True)
        await self.know(ENTRY, self.guard, [self.okra])
        await self.know(CONNECTION, self.guard_link, [self.okra])
        # both players' characters were told what the table was told (the DM kept Ken, the guard
        # and the guard's secret link back); the page is what each character knows (#3143)
        await self.tell_the_table(self.okra)
        await self.tell_the_table(self.wen)

    async def sql_one(self, statement: str, *args: Any) -> dict[str, Any]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(statement, args)
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def know(self, kind: str, statement: str, who: list[str]) -> None:
        await self.memory.set_statement_knowers(
            self.g, self.one.id, kind, statement, who, source=DM_SOURCE
        )

    async def join(self) -> Any:
        return await self.memory.join_same_person(self.g, self.one.id, self.guard, self.ken)

    async def bob(self, **kw: Any) -> dict[str, Any]:
        return await self.graph(self.player, center=self.mira, hops=2, **kw)

    def names(self, got: dict[str, Any]) -> dict[str, str]:
        return {n["id"]: n["name"] for n in got["nodes"]}

    async def overview_names(self, who: Any) -> dict[str, str]:
        got, _pages, _known = await relationships._overview_door(
            self.web, who, self.cref, None, 100, 100
        )  # (the door's own names, before they are grouped for the page)
        return {n["id"]: n["name"] for n in got["names"]}


class WhoKnewOnlyTheGuard(MyCharacters):
    async def test_the_guard_is_shown_and_never_ken(self) -> None:
        await self.join()
        got = await self.bob()
        nodes = self.names(got)
        (guard_node,) = [i for i, n in nodes.items() if n == self.label]
        self.assertTrue(guard_node.startswith("alias:"))
        self.assertNotIn(self.ken, nodes)
        self.assertNotIn("Ken", str(got))
        edge_ends = {(e["a"], e["b"]) for e in got["edges"]}
        self.assertTrue(any(guard_node in pair and self.mira in pair for pair in edge_ends))

    async def test_the_overview_says_the_same(self) -> None:
        await self.join()
        names = await self.overview_names(self.player)
        self.assertIn(self.label, names.values())
        self.assertNotIn(self.ken, names)
        self.assertNotIn("Ken", str(names))

    async def test_undo_gives_back_what_was_seen_before(self) -> None:
        before = await self.bob()
        written = await self.join()
        await self.memory.undo(self.g, self.one.id, written.batch, source=DM_SOURCE)
        after = await self.bob()
        self.assertEqual(self.names(after), self.names(before))
        self.assertNotIn("Ken", str(after))


class WhoKnewKen(MyCharacters):
    async def test_a_character_that_knows_both_sees_two_people(self) -> None:
        await self.know(ENTRY, self.ken, [self.okra])
        await self.join()
        names = await self.overview_names(self.player)
        self.assertEqual(names[self.ken], "Ken")
        self.assertIn(self.label, names.values())  # (and the guard, on a node of its own)


class NobodyElses(MyCharacters):
    async def test_another_players_knowledge_never_shows(self) -> None:
        await self.know(ENTRY, self.guard, [self.wen])  # only Wen knows now
        await self.know(CONNECTION, self.guard_link, [self.wen])
        await self.join()
        got = await self.bob()
        self.assertNotIn(self.ken, self.names(got))
        self.assertNotIn(self.label, str(got))
        self.assertNotIn("Ken", str(got))
        self.assertNotIn(self.ken, await self.overview_names(self.player))

    async def test_the_dm_sees_the_main_name(self) -> None:
        await self.join()
        got = await self.graph(center=self.mira, hops=2)
        self.assertEqual(self.names(got)[self.ken], "Ken")

    async def test_a_player_with_no_character_sees_nothing(self) -> None:
        """A member who plays no character gets an empty page, never the full graph (#3143): the
        page is what a player's own characters know, and they have none."""
        await self.know(ENTRY, self.guard, [])  # nobody
        await self.join()
        stranger = await self.signed(
            type(BOB_USER)(id=4242, name="Stranger", email="s@example.org"), MEMBER
        )
        self.assertEqual(await self.refused_web(stranger, center=self.mira, hops=2), "not_found")
        got = await self.graph(stranger)
        self.assertEqual((got["nodes"], got["edges"]), ([], []))
        self.assertNotIn("Ken", str(got))

    async def test_a_peek_behind_the_dm_screen_is_as_before(self) -> None:
        await self.join()
        peeked = await self.graph(self.player, center=self.mira, hops=2, view="full")
        self.assertEqual(self.names(peeked)[self.ken], "Ken")

    async def test_a_connection_of_a_hidden_entry_nobody_told_the_character_stays_hidden(
        self,
    ) -> None:
        other = await self.link("ally_of", self.ken, self.mira, "ken-mira")  # a table-wide link
        await self.join()
        got = await self.bob()
        shown = [x["id"] for e in got["edges"] for x in e["links"]]
        self.assertNotIn(other, shown)


class TheEdges(MyCharacters):
    async def test_a_player_is_never_told_an_entry_is_kept_from_the_players(self) -> None:
        await self.join()
        got = await self.bob()
        node = next(n for n in got["nodes"] if n["name"] == self.label)
        self.assertTrue(node["revealed"])
        # (and the DM's full view still says which entries are kept back)
        dm = await self.graph(center=self.mira, hops=2)
        self.assertFalse(next(n for n in dm["nodes"] if n["id"] == self.ken)["revealed"])

    async def test_no_page_is_offered_for_an_entry_known_only_by_its_other_name(self) -> None:
        await self.join()
        _got, pages, _known = await relationships._overview_door(
            self.web, self.player, self.cref, None, 100, 100
        )
        self.assertNotIn(self.ken, pages)

    async def test_the_default_centre_may_be_the_joined_entry_and_is_still_the_guard(self) -> None:
        await self.join()
        got = await self.graph(self.player, hops=2)  # no centre asked for
        self.assertNotIn("Ken", str(got))
        self.assertIn(self.label, self.names(got).values())

    async def test_one_character_knowing_ken_is_enough_to_show_his_name(self) -> None:
        orla = await self.add(
            "Orla", "character", role="player_character", played_by=BOB.id
        )  # (the same player's second character)
        await self.know(ENTRY, self.ken, [orla])
        await self.join()
        names = await self.overview_names(self.player)
        self.assertEqual(names[self.ken], "Ken")

    async def test_the_first_other_name_a_character_learned_is_the_one_shown(self) -> None:
        link = await self.link("knows", self.ken, self.mira, "ken-mira-secret", secret=True)
        await self.know(CONNECTION, link, [self.okra])
        await self.alias(self.ken, "Old Greycloak", secret=True)
        await self.alias(self.ken, "Ken the Younger", secret=True)
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.ken, include_secret=True
        )
        first = next(a.id for a in found if a.text == "Old Greycloak")
        second = next(a.id for a in found if a.text == "Ken the Younger")
        await self.know("alias", first, [self.okra])
        await self.know("alias", second, [self.okra])

        async def learned(alias_id: str, at: int) -> None:
            await self.sql(
                "UPDATE memory_knowers SET created_at = %s WHERE alias_id = %s", at, alias_id
            )

        await learned(first, 100)
        await learned(second, 200)
        self.assertEqual(self.names(await self.bob())[self.ken], "Old Greycloak")
        await learned(first, 300)  # now Ken the Younger was learned first
        self.assertEqual(self.names(await self.bob())[self.ken], "Ken the Younger")

    async def test_a_known_connection_to_a_shown_entry_shows_even_if_the_party_does_not_know_it(
        self,
    ) -> None:
        await self.join()
        got = await self.bob()
        (guard_node,) = [i for i, n in self.names(got).items() if n == self.label]
        ends = {(e["a"], e["b"]) for e in got["edges"]}
        self.assertTrue(any(guard_node in pair and self.mira in pair for pair in ends))


class HiddenAnchors(MyCharacters):
    async def sister(self) -> str:
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", role="sibling", anchor_id=self.ken
        )
        await self.sql(
            "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", made.entity.id
        )
        await self.know(ENTRY, made.entity.id, [self.okra])
        return str(made.entity.id)

    async def test_an_unnamed_persons_label_never_carries_a_hidden_anchors_name(self) -> None:
        sister = await self.sister()
        got = await self.bob()
        self.assertNotIn("Ken", str(got))
        names = await self.overview_names(self.player)
        self.assertEqual(names[sister], "someone's sister (name unknown)")
        self.assertNotIn("Ken", str(names))

    async def test_the_joined_label_is_rebuilt_too(self) -> None:
        sister = await self.sister()
        await self.memory.join_same_person(self.g, self.one.id, sister, self.ken)
        await self.know(
            ENTRY, self.ken, []
        )  # (nobody knows Ken by name; the sister's label is a secret other name now)
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.ken, include_secret=True
        )
        label_alias = next(a.id for a in found if a.text.endswith("(name unknown)"))
        await self.know("alias", label_alias, [self.okra])
        names = await self.overview_names(self.player)
        self.assertEqual(names[self.ken], "someone's sister (name unknown)")
        self.assertNotIn("Ken", str(names))


class TheDmAsAPlayer(MyCharacters):
    async def test_a_dms_players_view_is_not_widened_by_a_character_they_play(self) -> None:
        # the DM (Alice) also plays Okra
        await self.sql(
            "UPDATE memory_entities SET played_by = %s WHERE id = %s", ALICE.id, self.okra
        )
        await self.join()
        got = await self.graph(self.session, center=self.mira, hops=2, view="players")
        self.assertNotIn(self.ken, self.names(got))
        self.assertNotIn("Ken", str(got))


class TheDetail(MyCharacters):
    async def test_a_connections_note_is_withheld_when_an_end_is_shown_under_another_name(
        self,
    ) -> None:
        await self.sql(  # (on the catch-all the DM's own words are the connection's label)
            "UPDATE memory_relations SET predicate = %s, detail = %s WHERE id = %s",
            FALLBACK_PREDICATE,
            "owes a favour to Ken",
            self.guard_link,
        )
        await self.join()
        got = await self.bob()
        edge = next(x for e in got["edges"] for x in e["links"] if x["id"] == self.guard_link)
        self.assertNotIn("favour", edge["words"])
        self.assertNotIn("Ken", str(got))
        dm = await self.graph(center=self.mira, hops=2)
        self.assertIn("favour", str(dm))


class TableAnswers(MyCharacters):
    async def test_a_name_every_player_may_see_is_used_in_public_places(self) -> None:
        """Table answers and recaps are party-wide: after a join, Ken (hidden) is "someone"."""
        from dmbot.memory.gate import Viewer

        await self.join()
        shown = await self.memory.display_names(
            self.g, self.one.id, Viewer.the_party(), [self.ken], public=True
        )
        self.assertEqual(shown[self.ken], "someone")
        shown = await self.memory.display_names(
            self.g, self.one.id, Viewer.the_party(), [self.mira], public=True
        )
        self.assertEqual(shown[self.mira], "Mira Voss")


class WhenKenIsPublic(MyCharacters):
    """A name belongs to the fact (Supervisor, #3037): a character that knows Ken only through the
    guard's name sees those facts on a separate node, even though Ken is shown to everyone."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (the product's own reveal: every player character now has a row for Ken)
        await self.memory.set_hidden_from_players(self.g, self.one.id, self.ken, False, source="dm")
        self.ken_rook = await self.link("ally_of", self.ken, self.rook, "ken-rook")  # table-wide

    async def test_the_guard_is_a_separate_node_carrying_the_moved_link(self) -> None:
        await self.join()
        got = await self.bob()
        nodes = {n["id"]: n["name"] for n in got["nodes"]}
        (guard_node,) = [i for i, n in nodes.items() if n == self.label]
        self.assertTrue(guard_node.startswith("alias:"))
        self.assertEqual(nodes[self.ken], "Ken")  # (the public Ken, with his public facts)
        links = {x["id"]: (e["a"], e["b"]) for e in got["edges"] for x in e["links"]}
        self.assertIn(self.guard_link, links)
        self.assertEqual(set(links[self.guard_link]), {guard_node, self.mira})
        edges = [
            (e["a"], e["b"]) for e in got["edges"] for x in e["links"] if x["id"] == self.ken_rook
        ]
        self.assertEqual([set(pair) for pair in edges], [{self.ken, self.rook}])  # only on Ken

    async def test_the_overview_has_the_separate_node_too(self) -> None:
        await self.join()
        got, _pages, _known = await relationships._overview_door(
            self.web, self.player, self.cref, None, 100, 100
        )
        names = {n["id"]: n["name"] for n in got["names"]}
        self.assertEqual(names[self.ken], "Ken")
        (guard_node,) = [i for i, n in names.items() if n == self.label]
        self.assertIn(sorted([guard_node, self.mira]), [sorted(p) for p in got["pairs"]])

    async def test_the_dm_sees_one_entry(self) -> None:
        await self.join()
        got = await self.graph(center=self.mira, hops=2)
        self.assertEqual(self.names(got)[self.ken], "Ken")
        self.assertNotIn(self.label, self.names(got).values())

    async def test_a_character_that_knows_ken_by_name_sees_two_people(self) -> None:
        await self.know(ENTRY, self.ken, [self.okra])
        await self.join()
        got = await self.bob()
        nodes = self.names(got)
        self.assertEqual(nodes[self.ken], "Ken")
        (guard_node,) = [i for i, n in nodes.items() if n == self.label]
        links = {x["id"]: (e["a"], e["b"]) for e in got["edges"] for x in e["links"]}
        self.assertEqual(set(links[self.guard_link]), {guard_node, self.mira})  # on the guard


class ProposedAnchors(MyCharacters):
    async def test_a_proposed_anchors_name_never_shows_in_a_label(self) -> None:
        vex = await self.memory.add_entity(
            self.g, self.one.id, type="character", name="Spy Vex", source=DM_SOURCE,
            status="proposed", role="npc",
        )  # fmt: skip
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", role="sibling", anchor_id=vex.value.id
        )
        await self.sql(
            "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", made.entity.id
        )
        await self.know(ENTRY, made.entity.id, [self.okra])
        names = await self.overview_names(self.player)
        self.assertEqual(names[str(made.entity.id)], "someone's sister (name unknown)")
        self.assertNotIn("Vex", str(names))


class ByFact(MyCharacters):
    """The split is by FACT: the join records which name each moved connection came through."""

    async def test_a_public_from_creation_ken_gives_the_same_two_people(self) -> None:
        ken = await self.add("Kenneth", "character", role="npc")  # shown to everyone from the start
        guard = await self.memory.add_unnamed(
            self.g, self.one.id, what="porter at the east gate", role="guard"
        )
        link = await self.link("knows", guard.entity.id, self.mira, "porter-mira", secret=True)
        await self.know(ENTRY, guard.entity.id, [self.okra])
        await self.know(CONNECTION, link, [self.okra])
        await self.memory.join_same_person(self.g, self.one.id, guard.entity.id, ken)
        got = await self.bob()
        self.assertEqual(self.names(got).get(ken), None)  # (no public fact brings Kenneth here)
        self.assertNotIn("Kenneth", str(got))
        self.assertTrue(any(n.startswith("porter") for n in self.names(got).values()))

    async def test_the_connection_records_its_name_and_undo_takes_it_back(self) -> None:
        written = await self.join()
        row = await self.sql_one(
            "SELECT via_alias_id FROM memory_relations WHERE id = %s", self.guard_link
        )
        self.assertIsNotNone(row["via_alias_id"])
        await self.memory.undo(self.g, self.one.id, written.batch, source=DM_SOURCE)
        row = await self.sql_one(
            "SELECT via_alias_id FROM memory_relations WHERE id = %s", self.guard_link
        )
        self.assertIsNone(row["via_alias_id"])

    async def test_making_the_name_not_secret_folds_the_two_people_into_one(self) -> None:
        await self.memory.set_hidden_from_players(self.g, self.one.id, self.ken, False, source="dm")
        await self.know(ENTRY, self.ken, [self.okra])
        await self.join()
        before = await self.bob()
        self.assertIn(self.label, self.names(before).values())
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.ken, include_secret=True
        )
        label_alias = next(a for a in found if a.text == self.label)
        await self.memory.make_name_not_secret(
            self.g, self.one.id, label_alias.id, source=DM_SOURCE
        )
        after = await self.bob()
        self.assertNotIn(self.label, self.names(after).values())
        links = {x["id"]: (e["a"], e["b"]) for e in after["edges"] for x in e["links"]}
        self.assertEqual(set(links[self.guard_link]), {self.ken, self.mira})

    async def test_the_reader_lists_the_connection_on_the_guard_with_ken_public(self) -> None:
        await self.memory.set_hidden_from_players(self.g, self.one.id, self.ken, False, source="dm")
        await self.join()
        mine = await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)
        self.assertEqual(
            [(c.subject, c.object) for c in mine.connections], [(self.label, "Mira Voss")]
        )
        self.assertNotIn("Ken", repr(mine))

    async def test_a_centre_on_the_guards_own_node_is_asked_for_by_its_id(self) -> None:
        await self.join()
        got = await self.bob()
        (guard_node,) = [i for i, n in self.names(got).items() if n == self.label]
        again = await self.graph(self.player, center=guard_node, hops=2)
        self.assertEqual(again["center"], guard_node)
        self.assertIn(guard_node, self.names(again))
        self.assertNotIn("Ken", str(again))

    async def test_the_guards_node_is_the_kind_of_the_person_it_described(self) -> None:
        await self.join()
        got = await self.bob()
        node = next(n for n in got["nodes"] if n["name"] == self.label)
        self.assertEqual(node["kind"], "npc")
