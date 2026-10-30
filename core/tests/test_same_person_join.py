"""Joining an unnamed person to the named one it turned out to be (#3037): nothing widens. Whoever
knew only "the guard at the gate" knows that secret name and not "Ken"; whoever knew Ken keeps
what they knew; the DM sees both; Undo puts both back. Invented names."""

from __future__ import annotations

from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, Viewer
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_gate import Helpers
from tests.test_memory_store import DM, GUILD_A


class JoinBase(Helpers):
    """The setup the join tests share (no tests of its own, so subclasses do not repeat them)."""

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
        # (Ken's own name is a name too: a row on it counts as knowing the entry under that name)
        own = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        for alias in own:
            await self.know(ALIAS, alias.id, [self.sees_ken])

    async def join(self) -> object:
        return await self.memory.join_same_person(GUILD_A, self.c, self.guard, self.ken)

    async def view(self, pc: str) -> object:
        return await self.memory.visible_to(GUILD_A, self.c, Viewer.character(pc))

    async def alias_id(self) -> str:
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        (one,) = [a for a in found if a.text == self.label]
        return one.id


class TheJoin(JoinBase):
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


class TheNameEachViewerSees(JoinBase):
    """Which name a viewer is shown for the joined entry (Supervisor's ruling on #3037)."""

    async def name_for(self, viewer: Viewer, *, public: bool = False) -> str | None:
        got = await self.memory.display_names(GUILD_A, self.c, viewer, [self.ken], public=public)
        return got[self.ken]

    async def test_who_knew_only_the_guard_is_shown_the_guard_and_never_ken(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        await self.join()
        self.assertEqual(await self.name_for(Viewer.character(self.sees_guard)), self.label)

    async def test_a_ken_the_players_are_shown_is_named_ken_to_everyone(self) -> None:
        """A confirmed entry shown to the players is named by its main name (#3037 ruling)."""
        await self.join()
        self.assertEqual(await self.name_for(Viewer.character(self.sees_guard)), "Ken")

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


class TheCondition(JoinBase):
    async def test_the_unnamed_ones_condition_does_not_become_kens(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.guard, "dead", source="dm")
        await self.join()
        got = await self.memory.states(GUILD_A, self.c, [self.ken])
        self.assertIsNone(got.get(self.ken))  # (history only: the DM sets Ken's own)


class TheProbes(JoinBase):
    """What Supervisor's review of #3071 reproduced: a join must never teach, or take away,
    anything. Each of these failed on the first version."""

    async def test_a_connection_a_character_knew_survives_the_join_and_undo(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.know(CONNECTION, rel, [self.sees_guard])
        written = await self.join()
        seen = await self.view(self.sees_guard)
        self.assertTrue(seen.knows(CONNECTION, rel))  # type: ignore[attr-defined]
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        again = await self.view(self.sees_guard)
        self.assertTrue(again.knows(CONNECTION, rel))  # type: ignore[attr-defined]

    async def test_a_twin_fact_of_kens_does_not_become_secret(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        theirs = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.join()
        shown = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.ken, confirmed_only=True, include_secret=False
        )
        self.assertIn(theirs.value[0].id, [r.id for r in shown])  # (still public)

    async def test_an_entry_hanging_off_the_guard_never_shows_ken(self) -> None:
        dog = await self.memory.add_unnamed(
            GUILD_A, self.c, what="dog", anchor_id=self.guard, role="pet"
        )
        with self.assertRaises(MemoryRuleError) as why:
            await self.join()
        self.assertEqual(
            str(why.exception),
            "Something is described through this person (the dog). Name it or remove it first, "
            "so it isn't described through Ken.",
        )
        names = [label for _, label in await self.memory.unnamed(GUILD_A, self.c)]
        self.assertFalse(any("Ken" in label for label in names))
        self.assertTrue(dog.entity.id)

    async def test_the_guards_other_names_are_secret_on_ken_afterwards(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.guard, "the gatekeeper", kind="title", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        await self.join()
        public = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken)
        self.assertNotIn("the gatekeeper", [a.text for a in public])
        secret = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        self.assertIn("the gatekeeper", [a.text for a in secret])

    async def test_who_knew_ken_still_knows_ken_after_joining_a_hidden_guard(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.guard, True, source="dm")
        await self.join()
        seen = await self.view(self.sees_ken)
        self.assertTrue(seen.knows(ENTRY, self.ken))  # type: ignore[attr-defined]
        entry = await self.memory.entity(GUILD_A, self.c, self.ken)
        assert entry is not None
        self.assertFalse(entry.hidden_from_players)  # (the named entry keeps its own flags)

    async def test_the_wrong_kind_is_refused(self) -> None:
        sword = await self.add("The Sword", type="item", status=CONFIRMED)
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_same_person(GUILD_A, self.c, self.guard, sword)

    async def test_every_refusal(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        far = await self.add("Far Ken", campaign=other, type="character", status=CONFIRMED)
        waiting = await self.add("Pip", type="character", status="proposed")
        refused = [
            (self.guard, far),  # another campaign
            (self.guard, waiting),  # a proposed named entry
            (self.guard, self.guard),  # the same id
            (self.ken, self.guard),  # the wrong way round
        ]
        for a, b in refused:
            with self.subTest(a=a, b=b), self.assertRaises(MemoryRuleError):
                await self.memory.join_same_person(GUILD_A, self.c, a, b)
        await self.join()
        with self.assertRaises(MemoryRuleError):  # already merged
            await self.join()


class ThePublicName(JoinBase):
    async def test_a_public_place_shows_ken_when_ken_is_not_hidden(self) -> None:
        # (everyone knows Ken here: the join has nothing to hide, and "someone" would be wrong)
        for alias in await self.memory.aliases(
            GUILD_A, self.c, entity_id=self.ken, include_secret=True
        ):
            await self.know(ALIAS, alias.id, list(self.ids.values())[:2])
        await self.know(ENTRY, self.ken, [self.ids["okthandom"], self.ids["mara"]])
        await self.join()
        got = await self.memory.display_names(
            GUILD_A, self.c, Viewer.the_party(), [self.ken], public=True
        )
        self.assertEqual(got[self.ken], "Ken")

    async def test_a_merged_or_rejected_entry_has_no_name(self) -> None:
        await self.join()
        got = await self.memory.display_names(
            GUILD_A, self.c, Viewer.the_dm(), [self.guard, self.ken]
        )
        self.assertIsNone(got[self.guard])  # (merged into Ken)
        self.assertEqual(got[self.ken], "Ken")


class TheSecondReview(JoinBase):
    """What Supervisor's second review of #3071 found."""

    async def set_columns(self, entity_id: str, **columns: object) -> None:
        sets = ", ".join(f"{name} = %s" for name in columns)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                f"UPDATE memory_entities SET {sets} WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (*columns.values(), GUILD_A, self.c, entity_id),
            )

    async def test_undo_works_when_the_guard_had_a_public_other_name(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.guard, "the gatekeeper", kind="title", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        written = await self.join()
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")  # (was refused)
        back = await self.memory.aliases(GUILD_A, self.c, entity_id=self.guard)
        self.assertIn("the gatekeeper", [a.text for a in back])  # public again, on the guard
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 1)

    async def test_a_name_ken_already_had_keeps_its_flag_and_its_knowers(self) -> None:
        shared_ken = await self.memory.add_alias(
            GUILD_A, self.c, self.ken, "the gatekeeper", kind="title", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        theirs = await self.memory.add_alias(
            GUILD_A, self.c, self.guard, "the gatekeeper", kind="title", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        await self.know(ALIAS, shared_ken.value.id, [self.sees_ken])
        await self.know(ALIAS, theirs.value.id, [self.sees_guard])
        await self.join()
        names = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken)
        self.assertIn("the gatekeeper", [a.text for a in names])  # (still public: Ken's own)
        seen = await self.view(self.sees_guard)
        self.assertTrue(seen.knows(ALIAS, shared_ken.value.id))  # type: ignore[attr-defined]
        other = await self.view(self.sees_ken)
        self.assertTrue(other.knows(ALIAS, shared_ken.value.id))  # type: ignore[attr-defined]

    async def test_a_guard_connection_folding_into_kens_twin_keeps_its_knowers(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        ken_twin = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        mine = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.know(CONNECTION, str(mine.value[0].id), [self.sees_guard])
        await self.join()
        seen = await self.view(self.sees_guard)
        self.assertTrue(seen.knows(CONNECTION, str(ken_twin.value[0].id)))  # type: ignore[attr-defined]
        shown = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.ken, confirmed_only=True, include_secret=False
        )
        self.assertIn(ken_twin.value[0].id, [r.id for r in shown])  # (and Ken's stays public)

    async def test_no_extra_belief_lands_on_a_public_twin(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        ken_twin = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip

        async def beliefs() -> int:
            async with self.db.guild(GUILD_A) as conn:
                cur = await conn.execute(
                    "SELECT count(*) AS n FROM memory_beliefs WHERE guild_id = %s"
                    " AND campaign_id = %s AND relation_id = %s",
                    (GUILD_A, self.c, str(ken_twin.value[0].id)),
                )
                row = await cur.fetchone()
                return int(row["n"]) if row else -1

        before = await beliefs()
        await self.join()
        self.assertEqual(await beliefs(), before)

    async def test_an_entry_that_is_not_live_but_hangs_off_the_guard_is_left_alone(self) -> None:
        dog = await self.memory.add_unnamed(
            GUILD_A, self.c, what="dog", anchor_id=self.guard, role="pet"
        )
        await self.set_columns(dog.entity.id, status="rejected")
        await self.join()
        entry = await self.memory.entity(GUILD_A, self.c, dog.entity.id)
        assert entry is not None
        self.assertNotIn("Ken", entry.name)  # (not renamed, so nothing shows Ken if it comes back)
        self.assertEqual(entry.unnamed_anchor, self.guard)

    async def test_a_rejected_named_entry_and_a_person_are_refused(self) -> None:
        rejected = await self.add("Gone", type="character", role="npc", status=CONFIRMED)
        await self.set_columns(rejected, status="rejected")
        person = await self.add("Pat", type="character", role="npc", status=CONFIRMED)
        await self.set_columns(person, type="person")
        for named in (rejected, person):
            with self.subTest(named=named), self.assertRaises(MemoryRuleError):
                await self.memory.join_same_person(GUILD_A, self.c, self.guard, named)


class TheThirdReview(JoinBase):
    async def test_who_knew_the_guards_other_name_still_knows_it_after_the_join(self) -> None:
        gatekeeper = await self.memory.add_alias(
            GUILD_A, self.c, self.guard, "the gatekeeper", kind="title", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        alias = gatekeeper.value.id
        await self.know(ALIAS, alias, [self.sees_guard])
        await self.join()
        seen = await self.view(self.sees_guard)
        self.assertTrue(seen.knows(ALIAS, alias))  # type: ignore[attr-defined]
        other = await self.view(self.sees_ken)
        self.assertFalse(other.knows(ALIAS, alias))  # type: ignore[attr-defined]

    async def test_the_party_stops_believing_a_connection_that_moved_and_recall_drops_it(
        self,
    ) -> None:
        from dmbot.memory.models import KNOWN
        from dmbot.memory.recall import PLAYERS, RecallService

        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        rel = str(made.value[0].id)

        async def party_knows() -> bool:
            async with self.db.guild(GUILD_A) as conn:
                cur = await conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM (SELECT pb.confidence FROM memory_beliefs pb"
                    " JOIN memory_perspectives p ON p.guild_id = pb.guild_id"
                    " AND p.campaign_id = pb.campaign_id AND p.id = pb.perspective_id"
                    " WHERE pb.guild_id = %s AND pb.campaign_id = %s AND pb.relation_id = %s"
                    " AND p.kind = 'party' ORDER BY pb.seq DESC LIMIT 1) now"
                    " WHERE now.confidence >= %s) AS known",
                    (GUILD_A, self.c, rel, KNOWN),
                )
                row = await cur.fetchone()
                return bool(row and row["known"])

        self.assertTrue(await party_knows())  # (believed while it was the guard's)
        await self.join()
        self.assertFalse(await party_knows())  # (secret now, so the party's belief is ended)
        recall = await RecallService(self.memory).recall(
            GUILD_A, self.c, seeds=[self.ken], audience=PLAYERS, opted_in={7}
        )
        self.assertNotIn("Captain Vale", " ".join(i.text for i in recall.items))
