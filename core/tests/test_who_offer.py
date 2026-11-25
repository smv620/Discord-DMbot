"""Which names the "who is he?" check offers the AI (#2790 slice 2): the "Who is this?" ranking
(#3602) with the pronoun's context, at most five, written as the passage writes them. Pure: a
scripted stand-in for the AI, invented names, no network or database."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import unittest
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import background as bg
from dmbot.memory import who_ask, who_resolve
from dmbot.memory import who_ranking as ranking
from dmbot.memory.who_is_it import NOT_LISTED

TEXT = "Mara arrived from Rivermouth with Bram. Dusk fell over the road. She was the sister of Ken."
NAMES = ["Mara", "Bram", "Ken", "Rivermouth", "Tessaly Crane", "Oddo"]


def open_pronoun(text: str = TEXT, quote: str = "She was the sister of Ken") -> bg.Unsettled:
    held: list[bg.Unsettled] = []
    line = {"a": "Mara", "how": "family", "b": "Ken", "quote": quote}
    bg.read_connections(json.dumps({"connections": [line]}), text, unsettled=held)
    (u,) = held
    return u


def pool() -> list[ranking.Candidate]:
    return [
        ranking.Candidate("ken", "npc", ("Ken",), frozenset({"tess"})),
        ranking.Candidate("mara", "npc", ("Mara Voss", "Mara")),
        # (the players don't know him yet: still a candidate, as in most lore reads)
        ranking.Candidate("bram", "npc", ("Bram",), hidden=True),
        ranking.Candidate("river", "place", ("Rivermouth",)),
        # not in the passage, but family of the named end: "his sister" may be her
        ranking.Candidate("tess", "npc", ("Tessaly Crane",), frozenset({"ken"})),
        # not in the passage and tied to nobody in it: no reason to offer
        ranking.Candidate("oddo", "npc", ("Oddo",)),
    ]


class TheOffer(unittest.TestCase):
    def test_without_a_pool_it_offers_the_names_the_passage_holds_as_before(self) -> None:
        u = open_pronoun()
        self.assertEqual(
            who_resolve.offer_for(u, NAMES, None), ["Mara", "Bram", "Ken", "Rivermouth"]
        )

    def test_the_ranking_offers_the_scene_and_the_named_ends_family_never_the_end_itself(
        self,
    ) -> None:
        offered = who_resolve.offer_for(open_pronoun(), NAMES, pool())
        self.assertIn("Mara", offered)
        self.assertIn("Bram", offered)
        self.assertIn("Tessaly Crane", offered)  # tied to Ken: "his sister" may be her
        self.assertNotIn("Ken", offered)  # the named end is never who "she" is
        self.assertNotIn("Oddo", offered)  # nothing links him to the passage

    def test_a_he_or_she_is_never_a_place(self) -> None:
        self.assertNotIn("Rivermouth", who_resolve.offer_for(open_pronoun(), NAMES, pool()))

    def test_a_name_is_offered_as_the_passage_writes_it(self) -> None:
        offered = who_resolve.offer_for(open_pronoun(), NAMES, pool())
        self.assertIn("Mara", offered)
        self.assertNotIn("Mara Voss", offered)

    def test_at_most_five_names_are_offered(self) -> None:
        many = [f"Cast{i}" for i in range(9)]
        text = " and ".join(many) + " came to the hall. She was the sister of Ken."
        # (the same open pronoun, its passage now naming nine people)
        u = dataclasses.replace(open_pronoun(), passage=text)
        cands = [ranking.Candidate(n.lower(), "npc", (n,)) for n in many]
        cands.append(ranking.Candidate("ken", "npc", ("Ken",)))
        offered = who_resolve.offer_for(u, [*many, "Ken"], cands)
        self.assertEqual(len(offered), ranking.LIMIT)

    def test_a_hidden_entrys_secret_name_is_never_offered_even_in_the_passage(self) -> None:
        # The Veil is Bram's secret name: `names` leave it out, so no offer or card has it.
        text = "Mara arrived with The Veil. Dusk fell. She was the sister of Ken."
        u = dataclasses.replace(open_pronoun(), passage=text)
        cands = [*pool(), ranking.Candidate("veil", "npc", ("Bram", "The Veil"), hidden=True)]
        offered = who_resolve.offer_for(u, NAMES, cands)
        self.assertNotIn("The Veil", offered)
        got = asyncio.run(who_resolve.settle([u], Scripted("Bram"), NAMES, pool=cands))
        self.assertTrue(all("The Veil" not in p.candidates for p in got.picks))

    def test_the_passages_people_come_first_and_the_named_end_is_never_cut(self) -> None:
        people = ["Ann", "Bo", "Cy", "Di", "Ed", "Fay"]
        # (the end the reader named has the longest name and stands nearest the pronoun)
        text = " and ".join(people) + " met Wilhelmina Thorncastle. She was the sister of Ken."
        u = dataclasses.replace(
            open_pronoun(), passage=text,
            made=dataclasses.replace(open_pronoun().made, a="Wilhelmina Thorncastle"),
        )  # fmt: skip
        cands = [ranking.Candidate(n.lower(), "npc", (n,)) for n in people]
        cands += [
            ranking.Candidate("wil", "npc", ("Wilhelmina Thorncastle",)),
            ranking.Candidate("ken", "npc", ("Ken",), frozenset({"kin1", "kin2"})),
            ranking.Candidate("kin1", "npc", ("Olwen",), frozenset({"ken"})),
            ranking.Candidate("kin2", "npc", ("Perrin",), frozenset({"ken"})),
        ]
        names = [*people, "Wilhelmina Thorncastle", "Ken", "Olwen", "Perrin"]
        offered = who_resolve.offer_for(u, names, cands)
        self.assertEqual(offered[0], "Wilhelmina Thorncastle")
        self.assertEqual(len(offered), ranking.LIMIT)
        self.assertNotIn("Olwen", offered)  # family outside the passage only fills spare places
        self.assertIn("Fay", offered)  # nearest the pronoun among equals

    def test_family_outside_the_passage_fills_spare_places(self) -> None:
        offered = who_resolve.offer_for(open_pronoun(), NAMES, pool())
        self.assertEqual(offered[-1], "Tessaly Crane")

    def test_one_name_takes_one_place(self) -> None:
        twins = [*pool(), ranking.Candidate("mara2", "npc", ("Mara",))]
        offered = who_resolve.offer_for(open_pronoun(), NAMES, twins)
        self.assertEqual(offered.count("Mara"), 1)

    def test_the_named_end_is_known_by_any_of_its_names(self) -> None:
        # the AI wrote "Ken"; the entry is "Ken Arlow", also called Ken
        cands = [
            ranking.Candidate("ken", "npc", ("Ken Arlow", "Ken"), frozenset({"tess"})),
            ranking.Candidate("tess", "npc", ("Tessaly Crane",), frozenset({"ken"})),
            ranking.Candidate("mara", "npc", ("Mara",)),
        ]
        offered = who_resolve.offer_for(
            open_pronoun(), ["Ken Arlow", "Tessaly Crane", "Mara"], cands
        )
        self.assertNotIn("Ken Arlow", offered)
        self.assertIn("Tessaly Crane", offered)


class ThePool(unittest.TestCase):
    def test_a_name_this_read_found_is_its_own_candidate_unless_an_entry_has_it(self) -> None:
        got = who_resolve.pool_for(pool(), [("Bram", "npc"), ("Ysolde", "npc"), ("Ysolde", "npc")])
        ids = [c.id for c in got]
        self.assertEqual(ids.count("bram"), 1)
        self.assertEqual(sum(i.startswith("read:") for i in ids), 1)
        self.assertEqual(got[-1].names, ("Ysolde",))

    def test_a_found_name_with_no_kind_counts_as_a_character(self) -> None:
        (made,) = who_resolve.pool_for([], [("Ysolde", "")])
        self.assertEqual(made.kind, "character")

    def test_the_lookups_confirmed_ties_replace_the_entries_own(self) -> None:
        got = who_resolve.pool_for(pool(), [], {"bram": frozenset({"ken"})})
        ties = {c.id: c.ties for c in got}
        self.assertEqual(ties["bram"], frozenset({"ken"}))
        self.assertEqual(ties["tess"], frozenset())  # (no confirmed tie in the lookup)


class Scripted:
    """Records each question, answers the score with `top` at 90 and every sample with `top`."""

    def __init__(self, top: str) -> None:
        self.top = top
        self.texts: list[str] = []

    async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
        self.texts.append(text)
        if kwargs.get("schema") == who_ask.scores_schema():
            body: dict[str, Any] = {
                "candidates": [
                    {"name": self.top, "confidence": 90},
                    {"name": NOT_LISTED, "confidence": 5},
                ]
            }
        else:
            body = {"name": self.top}
        return Reply(json.dumps(body), cut=False)


class TheSettle(unittest.TestCase):
    def test_the_ai_is_offered_the_ranked_names_only(self) -> None:
        ai = Scripted("Mara")
        asyncio.run(who_resolve.settle([open_pronoun()], ai, NAMES, pool=pool()))
        first = ai.texts[0]
        self.assertIn("Tessaly Crane", first)
        self.assertNotIn("Oddo", first)
        self.assertNotIn("<names>\nKen", first)

    def test_the_answer_still_settles_the_connection(self) -> None:
        got = asyncio.run(
            who_resolve.settle([open_pronoun()], Scripted("Mara"), NAMES, pool=pool())
        )
        self.assertEqual(got.counts["accepted"], 1)
        (c,) = got.connections
        self.assertEqual((c.a, c.b), ("Mara", "Ken"))

    def test_with_the_ranking_the_card_follows_the_offered_order(self) -> None:
        u = open_pronoun()
        offered = who_resolve.offer_for(u, NAMES, pool())
        got = asyncio.run(who_resolve.settle([u], Scripted("Tessaly Crane"), NAMES, pool=pool()))
        (p,) = got.picks
        rest = [n for n in offered if n not in ("Tessaly Crane", "Mara")]
        self.assertEqual(list(p.candidates), ["Tessaly Crane", "Mara", *rest][: len(p.candidates)])

    def test_a_doubt_asks_the_dm_with_the_rankings_names(self) -> None:
        # the AI names someone the connection did not: a doubt, so the DM picks
        got = asyncio.run(
            who_resolve.settle([open_pronoun()], Scripted("Bram"), NAMES, pool=pool())
        )
        (p,) = got.picks
        self.assertEqual(p.candidates[0], "Bram")
        self.assertIn("Mara", p.candidates)


if __name__ == "__main__":
    unittest.main()
