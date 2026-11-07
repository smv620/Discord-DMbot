"""The DM's own Reputation wins on a `knows` line on the website too (#3642): the DM's Full View
reads the DM's word, with the link's `standing`; a player's own view, a peek and the DM's Players
View are given neither, and the door answers a DM only. Invented names; the door tests need
Postgres."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import standing
from dmbot.web import relationships, see_as
from tests.test_web_relationships import Web


def _knows(a: str, b: str, detail: str = "friendly") -> dict[str, Any]:
    return {
        "id": "k1", "a": a, "b": b, "predicate": "knows", "detail": detail, "secret": False,
        "at": 1,
    }  # fmt: skip


class TheWords(unittest.TestCase):
    """`edges_from` alone: the DM's word on the line, `standing` on the link, the DM's only."""

    def link(self, **kw: Any) -> dict[str, Any]:
        (edge,) = relationships.edges_from([_knows("a1", "b1")], can_edit=True, **kw)
        return dict(edge["links"][0])

    def test_the_dm_s_word_wins_for_the_dm(self) -> None:
        (edge,) = relationships.edges_from(
            [_knows("a1", "b1")], can_edit=True, is_dm=True, standing={("a1", "b1"): -40}
        )
        got = edge["links"][0]
        self.assertEqual((got["words"], got["standing"]), ("knows (distrusts)", "distrusts"))
        self.assertEqual(edge["label"], "knows (distrusts)")  # (the edge's label follows it)

    def test_the_other_way_stands_in(self) -> None:
        got = self.link(is_dm=True, standing={("b1", "a1"): 80})
        self.assertEqual(got["words"], "knows (devoted)")

    def test_no_standing_keeps_the_reader_s_word_and_sends_none(self) -> None:
        got = self.link(is_dm=True, standing={})
        self.assertEqual(got["words"], "knows (friendly)")
        self.assertNotIn("standing", got)

    def test_anyone_but_the_dm_is_given_neither(self) -> None:
        got = self.link(is_dm=False, standing={("a1", "b1"): -80})
        self.assertEqual(got["words"], "knows")
        self.assertNotIn("standing", got)

    def test_only_a_knows_line(self) -> None:
        row = {**_knows("a1", "b1", ""), "predicate": "rival_of"}
        (edge,) = relationships.edges_from(
            [row], can_edit=True, is_dm=True, standing={("a1", "b1"): -80}
        )
        self.assertNotIn("standing", edge["links"][0])
        self.assertEqual(edge["links"][0]["words"], "is a rival of")


class TheDoor(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", self.rook, source="dm", confidence=1.0,
            status="confirmed", detail="friendly",
        )  # fmt: skip
        self.knows = made.value[0].id
        await self.memory.set_impression(
            self.g, self.one.id, self.mira, self.rook, standing.HOSTILE, source="dm"
        )

    def line(self, page: dict[str, Any]) -> dict[str, Any]:
        return next(x for e in page["edges"] for x in e["links"] if x["id"] == self.knows)

    async def test_the_dm_s_full_view_reads_the_dm_s_word(self) -> None:
        got = self.line(await self.graph(center=self.mira, hops=1))
        self.assertEqual((got["words"], got["standing"]), ("knows (hostile)", "hostile"))

    async def test_a_player_s_own_view_and_peek_and_the_dm_s_players_view_get_neither(
        self,
    ) -> None:
        assert self.hero is not None
        await self.memory.set_statement_knowers(
            self.g, self.one.id, "connection", self.knows, [self.hero, self.rook], source="dm"
        )  # (every player character knows the line, so it is drawn)
        for who, view in ((self.player, None), (self.player, "full"), (None, "players")):
            with self.subTest(view=view):
                page = await self.graph(who, center=self.mira, hops=1, view=view)
                self.assertNotIn("hostile", str(page))
                self.assertNotIn("standing", str(page))
                self.assertEqual(self.line(page)["words"], "knows")  # (drawn, plain)

    async def test_the_door_answers_a_dm_only(self) -> None:
        self.assertEqual(
            await relationships.dm_standings(self.web, self.session, self.cref),
            {(self.mira, self.rook): -80},
        )
        self.assertEqual(await relationships.dm_standings(self.web, self.player, self.cref), {})

    async def test_a_story_feeling_is_not_the_dm_s(self) -> None:
        wrote = await self.memory.add_feeling(self.g, self.one.id, self.keep, self.rook, 40)
        self.assertIsNotNone(wrote)  # (it was written, so leaving it out means something)
        self.assertEqual(
            await relationships.dm_standings(self.web, self.session, self.cref),
            {(self.mira, self.rook): -80},
        )

    async def test_only_the_dm_s_latest_word_counts(self) -> None:
        await self.memory.set_impression(
            self.g, self.one.id, self.mira, self.rook, standing.FRIENDLY, source="dm"
        )
        got = self.line(await self.graph(center=self.mira, hops=1))
        self.assertEqual(got["words"], "knows (friendly)")

    async def test_see_as_gets_neither(self) -> None:
        for viewer in (see_as.PARTY, self.rook):
            with self.subTest(viewer):
                page = await see_as.read_see_as(
                    self.web, self.session, self.cref, viewer, center=self.mira, hops=1
                )
                self.assertNotIn("hostile", str(page))
                self.assertNotIn("standing", str(page))

    async def test_another_campaign_s_dm_is_given_nothing(self) -> None:
        """Bob is the DM of campaign two, not of this one: this door gives him nothing here."""
        self.assertEqual(await relationships.dm_standings(self.web, self.player, self.cref), {})
