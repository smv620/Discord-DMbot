"""What the Story Graph's doors may tell someone who is looking as the players do (#2989): a
player, a player asking for the players' view, and a DM in Players View. A name or connection the
players don't know must not be there at all: not on the map, not in a count, a pair, the size, the
cap or the walk (a hidden name must not change which names are "within N steps"). The DM's Full
View is the only one that sees them. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class PlayersViewKeepsHiddenThingsOut(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Mira knows the Keep and Rook. Four things the players don't know, each joined to Mira:
        self.unknown = await self.add("Veiled Duke", "npc")  # 1. a name the players don't know yet
        self.limited = await self.add("Council Seat", "place")  # 3. limited to some players
        self.held_from = await self.add("Old Crypt", "place")  # 3. hidden from players
        self.behind = await self.add(
            "Beyond the Duke", "npc"
        )  # 4. only reachable through a hidden name
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = ANY(%s)",
            [self.unknown, self.held_from],
        )
        await self.sql(
            "UPDATE memory_entities SET audience_limited = true WHERE id = %s", self.limited
        )
        await self.link("knows", self.mira, self.unknown, "to-unknown")
        await self.link("knows", self.mira, self.limited, "to-limited")
        await self.link("knows", self.mira, self.held_from, "to-held")
        await self.link(
            "knows", self.unknown, self.behind, "behind"
        )  # only through the hidden name
        # 2. a connection between two names the players know, which they don't know yet.
        made = await self.memory.add_relation(
            self.g, self.one.id, self.rook, "enemy_of", self.keep, source="dm", confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        self.links["unrevealed"] = made.value[0].id
        # the players page is what Bob's hero knows: none of the four, nor the held-back link
        for hidden in (self.unknown, self.limited, self.held_from):
            await self.keep_from_bob(hidden)
        await self.keep_link_from_bob(self.links["unrevealed"])
        self.every_hidden_name = {self.unknown, self.limited, self.held_from, self.behind}
        self.hidden_entities = {self.unknown, self.limited, self.held_from}  # hidden themselves
        self.hidden_links = {
            self.links[k] for k in ("to-unknown", "to-limited", "to-held", "behind", "unrevealed")
        }

    async def audiences(self) -> dict[str, Any]:
        """The ways of looking as the players do."""
        return {
            "a player": dict(who=self.player),
            "a player asking for the players' view": dict(who=self.player, view="players"),
            "a DM in Players View": dict(who=self.session, view="players"),
        }

    async def test_the_map_has_none_of_it_for_anyone_looking_as_the_players_do(self) -> None:
        for label, ask in (await self.audiences()).items():
            for hops in (1, 2, 3):
                got = await self.graph(center=self.mira, hops=hops, **ask)
                seen = {n["id"] for n in got["nodes"]}
                self.assertFalse(seen & self.every_hidden_name, (label, hops))
                links = {x["id"] for e in got["edges"] for x in e["links"]}
                self.assertFalse(links & self.hidden_links, (label, hops))
                self.assertTrue(all(n.get("revealed", True) for n in got["nodes"]), label)
                self.assertTrue(all(x["revealed"] for e in got["edges"] for x in e["links"]), label)
                for e in got["edges"]:  # no line to nowhere
                    self.assertTrue({e["a"], e["b"]} <= seen, (label, hops))

    async def test_a_hidden_name_does_not_change_which_names_are_within_the_steps(self) -> None:
        for label, ask in (await self.audiences()).items():
            got = await self.graph(center=self.mira, hops=3, **ask)
            self.assertNotIn(
                self.behind, {n["id"] for n in got["nodes"]}, label
            )  # not "2 steps away"

    async def test_the_size_and_the_overview_count_none_of_it(self) -> None:
        full = await relationships.read_overview(self.web, self.session, self.cref)
        for label, ask in (await self.audiences()).items():
            who = ask["who"]
            size = await relationships.read_size(
                self.web, who, self.cref, center=self.mira, hops=3, view=ask.get("view")
            )
            full_size = await relationships.read_size(
                self.web, self.session, self.cref, center=self.mira, hops=3
            )
            self.assertEqual(full_size["names"] - size["names"], 4, label)
            view = await relationships.read_overview(self.web, who, self.cref, view=ask.get("view"))
            listed = {n["id"] for n in [*view.get("names", []), *view.get("tray", [])]}
            # (the name behind the Duke is not hidden itself: players may know it, not through him)
            self.assertFalse(listed & (self.every_hidden_name - {self.behind}), label)
            self.assertIn(
                self.behind, {n["id"] for n in view["tray"]}, label
            )  # unconnected for them
            self.assertLess(view["counts"]["names"], full["counts"]["names"], label)
            self.assertLess(view["counts"]["connections"], full["counts"]["connections"], label)
            self.assertEqual(view["view"], "players", label)

    async def test_only_a_dm_in_full_view_gets_them_and_flagged(self) -> None:
        got = await self.graph(center=self.mira, hops=3)
        by_id = {n["id"]: n for n in got["nodes"]}
        self.assertTrue(self.every_hidden_name <= set(by_id))
        self.assertFalse(by_id[self.unknown]["revealed"])
        self.assertFalse(by_id[self.limited]["revealed"])
        links = {x["id"]: x for e in got["edges"] for x in e["links"]}
        self.assertTrue(self.hidden_links <= set(links))
        self.assertFalse(links[self.links["unrevealed"]]["revealed"])

    async def test_find_a_name_offers_only_what_the_players_know_in_their_view(self) -> None:
        for label, ask in (await self.audiences()).items():
            for word in ("Duke", "Crypt", "Council", "Mira", "duke"):
                found = await relationships.find_names(
                    self.web, ask["who"], self.cref, word, view=ask.get("view")
                )
                self.assertFalse({n["id"] for n in found} & self.hidden_entities, (label, word))
            found = await relationships.find_names(
                self.web, ask["who"], self.cref, "mira", view=ask.get("view")
            )
            self.assertEqual([n["name"] for n in found], ["Mira Voss"], label)
        # A DM in Full View is offered all of them, as the whole list is theirs.
        for word, wanted in (
            ("duke", self.unknown),
            ("council", self.limited),
            ("crypt", self.held_from),
        ):
            every = await relationships.find_names(self.web, self.session, self.cref, word)
            self.assertIn(wanted, {n["id"] for n in every}, word)
        self.assertEqual(
            await relationships.find_names(self.web, self.session, self.cref, "  "), []
        )

    async def test_find_a_name_refuses_what_every_door_refuses(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await relationships.find_names(self.web, self.player, self.cref, "mira", view="nope")
        self.assertEqual(caught.exception.code, "bad_view")
