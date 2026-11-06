"""How one name feels about another is the DM's Reputation (#3610): a `knows` line's feeling
("hostile") reaches the DM and nobody else. Pure: every door that words a connection is checked
for a player and for the DM. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import gate
from dmbot.memory.link_words import link_words
from dmbot.memory.models import Relation
from dmbot.ui.name_card import fact_words
from dmbot.web import graph_view
from dmbot.web import node_summary_run as run
from dmbot.web.relationships import edges_from
from tests.test_node_summary_run import KESH, MIRA, PC, world


def source(feeling: str = "hostile") -> tuple[dict[str, Any], dict[str, Any]]:
    """Mira knows Kesh, with a feeling; both and the player character are known to the party."""
    src, kn = world([("l1", MIRA, KESH, "knows")], {"l1": [PC]})
    src["links"][0]["detail"] = feeling
    return src, kn


def edge_detail(viewer: gate.Viewer) -> str:
    src, kn = source()
    graph = graph_view.build(src, kn, viewer)
    return str(next(e for e in graph.edges if e["id"] == "l1")["detail"])


class TheGraphDoor(unittest.TestCase):
    def test_a_player_gets_no_feeling_on_a_knows_link(self) -> None:
        self.assertEqual(edge_detail(gate.Viewer.player([PC])), "")

    def test_seeing_as_a_character_or_the_party_is_a_players_view(self) -> None:
        self.assertEqual(edge_detail(gate.Viewer.character(PC)), "")
        self.assertEqual(edge_detail(gate.Viewer.the_party()), "")

    def test_the_dm_keeps_it(self) -> None:
        self.assertEqual(edge_detail(gate.Viewer.the_dm()), "hostile")


class TheNodeSummary(unittest.TestCase):
    def test_a_players_summary_says_nothing_of_how_they_feel(self) -> None:
        src, kn = source()
        graph = graph_view.build(src, kn, gate.Viewer.player([PC]))
        text = " ".join(str(e.get("detail")) for e in run._edges(graph))
        self.assertNotIn("hostile", text)


class TheWords(unittest.TestCase):
    def test_plain_knows_for_everyone_but_a_dm_screen(self) -> None:
        self.assertEqual(link_words("knows", "hostile"), "knows")
        self.assertEqual(link_words("knows", "hostile", feeling=True), "knows (hostile)")

    def test_the_discord_card_follows_the_same_rule(self) -> None:
        r = Relation(
            id="r1", subject_id="a", predicate="knows", object_id="b", detail="hostile",
            confidence=1.0, status="confirmed", source="dm", mention_ids=(),
            from_session_at=None, to_session_at=None, from_game_time=None,
            to_game_time=None, secret=False, created_at=0,
        )  # fmt: skip
        self.assertEqual(fact_words(r), "knows")
        self.assertEqual(fact_words(r, feeling=True), "knows (hostile)")

    def test_the_website_lines_follow_it_too(self) -> None:
        row = {
            "id": "c1", "a": "x", "b": "y", "predicate": "knows", "detail": "hostile",
            "secret": False, "at": 1,
        }  # fmt: skip
        players = edges_from([row], can_edit=False)[0]["links"][0]["words"]
        dm = edges_from([row], can_edit=True, is_dm=True)[0]["links"][0]["words"]
        self.assertEqual((players, dm), ("knows", "knows (hostile)"))


class TheDiscordCardRows(unittest.TestCase):
    """The card's rows (also Show all and the Edit menu) read the feeling for the DM alone."""

    def rows(self, *, secrets: bool) -> list[str]:
        from types import SimpleNamespace

        from dmbot.memory.models import CONFIRMED
        from dmbot.ui.name_card import _rows

        def entity(eid: str, name: str) -> Any:
            return SimpleNamespace(id=eid, name=name, type="character", status=CONFIRMED)

        mira, kesh = entity("m" * 32, "Mira"), entity("k" * 32, "Kesh")
        lookup: Any = SimpleNamespace(entities={mira.id: mira, kesh.id: kesh}, guarded=frozenset())
        r = Relation(
            "x" * 32, mira.id, "knows", kesh.id, "hostile", 1.0, CONFIRMED, "dm", (),
            None, None, None, None, False, 0,
        )  # fmt: skip
        return [row.words for row in _rows(lookup, mira.id, [r], secrets)]

    def test_a_player_reads_plain_knows(self) -> None:
        self.assertEqual(self.rows(secrets=False), ["knows"])

    def test_the_dm_reads_the_feeling(self) -> None:
        self.assertEqual(self.rows(secrets=True), ["knows (hostile)"])
