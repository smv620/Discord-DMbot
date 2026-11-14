"""Telling one character "the guard was Ken" (#3089): that character sees one person, the others
still see two, the DM sees one; Undo and Take back restore two; the door cuts what a player gets to
their own characters. Product reveals throughout. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import gate
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.web.recaps import _scoped
from tests.test_recap_web import BOB
from tests.test_web_my_characters_byfact import ByFactBase


class Rule(unittest.TestCase):
    """The gate alone: a told name draws no person of its own, and only for who was told."""

    def view(self, viewer: gate.Viewer) -> gate.Visible:
        data = gate.Statements(
            knowers={(gate.ALIAS, "a1"): {"c1": 1.0, "c2": 1.0}, (gate.ENTRY, "ken"): {"c1": 1.0}},
            aliases={"a1": ("ken",)},
            player_characters=frozenset({"c1", "c2"}),
            told={"a1": frozenset({"c1"})},
        )
        return gate.visible(viewer, data)

    def split(self, seen: gate.Visible) -> dict[str, str]:
        links = [{"id": "l1", "via": "a1", "secret": True, "unrevealed": False}]
        aliases = [{"id": "a1", "entity_id": "ken", "secret": True, "status": "confirmed"}]
        return dict(gate.split_connections(seen, links, aliases, {"a1": "the guard"}).links)

    def test_the_told_character_gets_no_person_of_its_own(self) -> None:
        self.assertEqual(self.split(self.view(gate.Viewer.character("c1"))), {})

    def test_another_character_still_gets_two(self) -> None:
        # (c2 knows the connection too)
        data = gate.Statements(
            knowers={
                (gate.ALIAS, "a1"): {"c2": 1.0},
                (gate.CONNECTION, "l1"): {"c2": 1.0},
            },
            aliases={"a1": ("ken",)},
            connections={"l1": ("g", "m")},
            player_characters=frozenset({"c1", "c2"}),
            told={"a1": frozenset({"c1"})},
        )
        seen = gate.visible(gate.Viewer.character("c2"), data)
        self.assertEqual(self.split(seen), {"l1": "a1"})

    def test_the_table_needs_every_character_told(self) -> None:
        self.assertEqual(self.view(gate.Viewer.the_table(["c1", "c2"])).told, frozenset())
        self.assertEqual(self.view(gate.Viewer.the_table(["c1"])).told, frozenset({"a1"}))

    def test_the_dm_has_nothing_to_fold(self) -> None:
        self.assertEqual(self.view(gate.Viewer.the_dm()).told, frozenset())


class Telling(ByFactBase):
    async def tell(self, character: str | None = None) -> Any:
        alias = await self.guard_alias(self.ken)
        return await self.memory.tell_character(
            self.g, self.one.id, alias.id, character or self.okra, source=DM_SOURCE
        )

    async def test_the_told_character_sees_one_person_and_the_others_two(self) -> None:
        await self.join()
        before = await self.report()
        self.on_the_guard_not_ken(before)
        await self.tell()
        after = await self.report()
        for key in ("graph_links", "wide_links"):
            self.assertIn(self.ken, after[key][self.guard_link], key)  # on Ken's own node now
            self.assertFalse(any(str(x).startswith("alias:") for x in after[key][self.guard_link]))
        self.assertIn(("Ken", "knows", "Mira Voss"), after["mine"])
        self.assertNotIn((self.label, "knows", "Mira Voss"), after["mine"])

    async def test_another_player_still_sees_two_people(self) -> None:
        await self.join()
        await self.know(gate.ALIAS, (await self.guard_alias(self.ken)).id, [self.okra, self.wen])
        await self.tell()  # Okra only; Wen holds the name too
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_told WHERE campaign_id = %s", (self.one.id,)
            )
            self.assertEqual((await cur.fetchone())["n"], 1)  # type: ignore[index]
        # Wen's view, worked out by the gate for Wen's characters alone
        seen_wen = await self.memory.visible_to(
            self.g, self.one.id, gate.Viewer.character(self.wen)
        )
        self.assertEqual(seen_wen.told, frozenset())
        seen_okra = await self.memory.visible_to(
            self.g, self.one.id, gate.Viewer.character(self.okra)
        )
        self.assertEqual(len(seen_okra.told), 1)

    async def test_telling_gives_the_name_only_and_no_fact(self) -> None:
        await self.join()
        facts = await self.memory.statement_knowers(
            self.g, self.one.id, gate.CONNECTION, [self.guard_link]
        )
        await self.tell()
        self.assertEqual(
            await self.memory.statement_knowers(
                self.g, self.one.id, gate.CONNECTION, [self.guard_link]
            ),
            facts,
        )  # (nothing about the connections changed)
        entry = await self.memory.statement_knowers(self.g, self.one.id, gate.ENTRY, [self.ken])
        self.assertEqual(entry[self.ken], [self.okra])

    async def test_telling_twice_changes_nothing_and_a_wrong_telling_is_refused(self) -> None:
        await self.join()
        self.assertTrue((await self.tell()).value)
        self.assertFalse((await self.tell()).value)
        alias = await self.guard_alias(self.ken)
        with self.assertRaises(MemoryRuleError):  # Wen does not hold the name
            await self.memory.tell_character(
                self.g, self.one.id, alias.id, self.wen, source=DM_SOURCE
            )
        with self.assertRaises(MemoryRuleError):  # only the DM
            await self.memory.tell_character(
                self.g, self.one.id, alias.id, self.okra, source="extractor"
            )

    async def test_undo_gives_back_two_people(self) -> None:
        await self.join()
        before = await self.report()
        written = await self.tell()
        await self.memory.undo(self.g, self.one.id, written.batch, source=DM_SOURCE)
        self.assertEqual(await self.report(), before)

    async def test_take_back_removes_the_telling_and_the_entry_it_gave(self) -> None:
        await self.join()
        before = await self.report()
        await self.tell()
        alias = await self.guard_alias(self.ken)
        await self.memory.take_back_told(self.g, self.one.id, alias.id, self.okra, source=DM_SOURCE)
        self.assertEqual(await self.report(), before)
        with self.assertRaises(MemoryRuleError):  # nothing left to take back
            await self.memory.take_back_told(
                self.g, self.one.id, alias.id, self.okra, source=DM_SOURCE
            )

    async def test_take_back_keeps_a_row_the_character_already_had(self) -> None:
        await self.join()
        await self.know(gate.ENTRY, self.ken, [self.okra])  # Okra knew Ken's name already
        await self.tell()
        alias = await self.guard_alias(self.ken)
        await self.memory.take_back_told(self.g, self.one.id, alias.id, self.okra, source=DM_SOURCE)
        entry = await self.memory.statement_knowers(self.g, self.one.id, gate.ENTRY, [self.ken])
        self.assertEqual(entry[self.ken], [self.okra])


class WhenACharacterChanges(Telling):
    async def told_rows(self) -> list[tuple[str, str]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT alias_id, character_id FROM memory_told WHERE campaign_id = %s",
                (self.one.id,),
            )
            return [(str(r["alias_id"]), str(r["character_id"])) for r in await cur.fetchall()]

    async def test_a_merged_character_takes_its_tellings_to_the_one_kept(self) -> None:
        await self.join()
        await self.tell()
        alias = await self.guard_alias(self.ken)
        twin = await self.add("Okra the Younger", "character", role="player_character")
        await self.memory.merge(
            self.g, self.one.id, twin, self.okra, source=DM_SOURCE, dm_said_same=True
        )
        self.assertEqual(await self.told_rows(), [(alias.id, twin)])


class OnTheDoor(ByFactBase):
    async def tell(self, character: str) -> None:
        alias = await self.guard_alias(self.ken)
        await self.memory.tell_character(self.g, self.one.id, alias.id, character, source=DM_SOURCE)

    async def door(self, who: Any) -> list[list[str]]:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute("SELECT dmbot_web_told(%s, %s) AS t", (self.g, self.one.id))
            found = await cur.fetchone()
        assert found is not None and found["t"] is not None
        return [list(r) for r in found["t"]["told"]]

    async def test_a_player_gets_only_their_own_characters_rows(self) -> None:
        await self.join()
        alias = await self.guard_alias(self.ken)
        await self.know(gate.ALIAS, alias.id, [self.okra, self.wen])
        await self.tell(self.okra)
        await self.tell(self.wen)
        everyone = await self.door(self.session)
        self.assertEqual(
            {tuple(r) for r in everyone}, {(alias.id, self.okra), (alias.id, self.wen)}
        )
        bobs = await self.door(self.player)  # Bob plays Okra; someone else plays Wen
        self.assertEqual(bobs, [[alias.id, self.okra]])
        self.assertEqual(BOB.id, self.player.user_id)
