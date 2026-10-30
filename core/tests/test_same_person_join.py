"""Joining an unnamed person to the named one it turned out to be (#3037): nothing widens. Whoever
knew only "the guard at the gate" knows that secret name and not "Ken"; whoever knew Ken keeps
what they knew; the DM sees both; Undo puts both back. Invented names."""

from __future__ import annotations

from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, Viewer
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A


class TheJoin(Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        self.ken = await self.add("Ken", type="character", role="npc", status=CONFIRMED)
        (shown,) = await self.memory.unnamed(GUILD_A, self.c)
        self.label = shown[0].name  # (what the join makes the secret other name from)
        self.sees_guard = self.ids["okthandom"]  # knew only the unnamed guard
        self.sees_ken = self.ids["mara"]  # knew Ken
        await self.know(ENTRY, self.guard, [self.sees_guard])
        await self.know(ENTRY, self.ken, [self.sees_ken])

    async def join(self) -> object:
        return await self.memory.join_same_person(GUILD_A, self.c, self.guard, self.ken)

    async def view(self, pc: str) -> object:
        return await self.memory.visible_to(GUILD_A, self.c, Viewer.character(pc))

    async def alias_id(self) -> str:
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        (one,) = [a for a in found if a.text == self.label]
        return one.id

    async def test_the_description_becomes_a_secret_other_name_of_the_named_entry(self) -> None:
        await self.join()
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        (alias,) = [a for a in found if a.text == self.label]
        self.assertEqual((alias.secret, alias.status), (True, CONFIRMED))
        public = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken)
        self.assertNotIn(self.label, [a.text for a in public])  # never shown to players

    async def test_who_knew_only_the_guard_knows_the_secret_name_and_not_ken(self) -> None:
        await self.join()
        seen = await self.view(self.sees_guard)
        # (the entry is known to them only through that name: no row says they know it by its own)
        self.assertNotIn((ENTRY, self.ken), seen.sureness)  # type: ignore[attr-defined]
        alias = await self.alias_id()
        self.assertTrue(seen.knows(ALIAS, alias))  # type: ignore[attr-defined]

    async def test_who_knew_ken_keeps_what_they_knew_and_learns_nothing_of_the_guard(self) -> None:
        await self.join()
        seen = await self.view(self.sees_ken)
        self.assertTrue(seen.knows(ENTRY, self.ken))  # type: ignore[attr-defined]
        self.assertFalse(seen.knows(ALIAS, await self.alias_id()))  # type: ignore[attr-defined]

    async def test_the_dm_sees_both(self) -> None:
        await self.join()
        dm = await self.memory.visible_to(GUILD_A, self.c, Viewer.the_dm())
        self.assertTrue(dm.knows(ENTRY, self.ken))
        self.assertTrue(dm.knows(ALIAS, await self.alias_id()))

    async def test_undo_puts_both_sides_back(self) -> None:
        written = await self.join()
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        guard = await self.view(self.sees_guard)
        self.assertTrue(guard.knows(ENTRY, self.guard))  # type: ignore[attr-defined]
        ken = await self.view(self.sees_ken)
        self.assertTrue(ken.knows(ENTRY, self.ken))  # type: ignore[attr-defined]
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 1)

    async def test_it_refuses_a_pair_that_is_not_unnamed_and_named(self) -> None:
        other = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        for a, b in ((self.ken, other), (self.ken, self.guard)):
            with self.subTest(a=a, b=b), self.assertRaises(MemoryRuleError):
                await self.memory.join_same_person(GUILD_A, self.c, a, b)

    async def test_a_connection_that_moves_to_ken_stays_hidden_from_players_who_did_not_know_it(
        self,
    ) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        rel = str(made.value[0].id)
        before = await self.view(self.sees_ken)
        self.assertFalse(before.knows(CONNECTION, rel))  # type: ignore[attr-defined]
        await self.join()
        for pc in (self.sees_ken, self.sees_guard, self.ids["okthandom"], self.ids["mara"]):
            with self.subTest(pc=pc):
                seen = await self.view(pc)
                self.assertFalse(seen.knows(CONNECTION, rel))  # type: ignore[attr-defined]

    async def test_a_moved_connection_is_secret_so_the_old_readers_do_not_show_it(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.join()
        shown = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.ken, confirmed_only=True, include_secret=False
        )
        self.assertEqual([r for r in shown if r.id == made.value[0].id], [])
        everything = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.ken, confirmed_only=True, include_secret=True
        )
        self.assertEqual(len([r for r in everything if r.id == made.value[0].id]), 1)  # the DM's


class TheNameEachViewerSees(TheJoin):
    """Which name a viewer is shown for the joined entry (Supervisor's ruling on #3037)."""

    async def name_for(self, viewer: Viewer, *, public: bool = False) -> str | None:
        got = await self.memory.display_names(GUILD_A, self.c, viewer, [self.ken], public=public)
        return got[self.ken]

    async def test_who_knew_only_the_guard_is_shown_the_guard_and_never_ken(self) -> None:
        await self.join()
        self.assertEqual(await self.name_for(Viewer.character(self.sees_guard)), self.label)

    async def test_who_knew_ken_is_shown_ken(self) -> None:
        await self.join()
        self.assertEqual(await self.name_for(Viewer.character(self.sees_ken)), "Ken")

    async def test_someone_who_knew_neither_is_shown_nothing(self) -> None:
        await self.join()
        stranger = await self.add(
            "Zara", type="character", role="player_character", played_by=3, status=CONFIRMED
        )
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        self.assertIsNone(await self.name_for(Viewer.character(stranger)))

    async def test_the_dm_sees_the_main_name(self) -> None:
        await self.join()
        self.assertEqual(await self.name_for(Viewer.the_dm()), "Ken")

    async def test_a_public_place_never_says_ken_while_ken_is_hidden(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        await self.join()
        self.assertEqual(await self.name_for(Viewer.the_party(), public=True), "someone")

    async def test_after_undo_each_sees_what_they_saw_before(self) -> None:
        written = await self.join()
        assert written.batch is not None  # type: ignore[attr-defined]
        await self.memory.undo(GUILD_A, self.c, written.batch, source="dm")  # type: ignore[attr-defined]
        got = await self.memory.display_names(
            GUILD_A, self.c, Viewer.character(self.sees_guard), [self.guard]
        )
        self.assertEqual(got[self.guard], self.label)
        self.assertEqual(await self.name_for(Viewer.character(self.sees_ken)), "Ken")
