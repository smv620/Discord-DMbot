"""A character's lineage and class on the website (#3925, migration 0587): the door answers a DM of
the campaign and nobody else, with the rows Discord's name card reads (species and class only), and
nothing for an entry that is not a kept character or belongs to another campaign. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.web import node_traits, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web


class Traits(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.torvin = await self.add("Torvin", "character")
        await self.memory.set_rule_link(
            self.g, self.one.id, self.torvin, "species", "Dwarf", source="dm",
            rules_source="SRD 5.2", edition="2024", known=True,
        )  # fmt: skip
        await self.memory.set_rule_link(
            self.g, self.one.id, self.torvin, "class", "Cleric", source="dm",
            rules_source="SRD 5.2", edition="2014", known=True,
        )  # fmt: skip
        await self.memory.set_rule_link(
            self.g, self.one.id, self.torvin, "class", "Skald", source="dm", known=False
        )

    async def read(self, node: str, who: Any = None) -> dict[str, Any]:
        return await node_traits.read(self.web, who or self.session, self.cref, node)

    async def test_a_dm_gets_the_species_and_classes_with_their_flags(self) -> None:
        got = await self.read(self.torvin)
        self.assertEqual(
            got["traits"],
            [
                {"kind": "species", "name": "Dwarf", "known": True, "edition": "2024"},
                {"kind": "class", "name": "Cleric", "known": True, "edition": "2014"},
                {"kind": "class", "name": "Skald", "known": False, "edition": ""},
            ],
        )

    async def test_an_entry_with_none_or_that_is_not_a_character_gets_an_empty_list(self) -> None:
        keep = await self.add("Greywater Keep", "place")
        mira = await self.add("Mira", "character")
        for node in (keep, mira):
            self.assertEqual((await self.read(node))["traits"], [])

    async def test_a_merged_character_gets_nothing(self) -> None:
        old = await self.add("Old Torvin", "character")
        await self.sql(
            "UPDATE memory_rule_links SET entity_id = %s WHERE entity_id = %s", old, self.torvin
        )
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.torvin, old,
        )  # fmt: skip
        self.assertEqual((await self.read(old))["traits"], [])

    async def test_a_player_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await self.read(self.torvin, bob)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_dm_of_another_campaign_is_refused_for_this_ones_character(self) -> None:
        other = f"{self.g}.{self.two.id}"  # (not a campaign this DM runs)
        with self.assertRaises(RecapError) as err:
            await node_traits.read(self.web, self.session, other, self.torvin)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_bad_id_is_not_found(self) -> None:
        for node in ("nope", "alias:" + "a" * 32, ""):
            with self.assertRaises(RecapError) as err:
                await self.read(node)
            self.assertEqual(err.exception.code, "not_found")
